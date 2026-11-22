"""
363_g2_inventario_origem - inventario SO LEITURA (Graph GET) das fontes da operacao G2:
  - Programa:      biblioteca Programa, pasta "3. Desenvolvimento/G2 -  Engenharia" (sem _Obsoleto e _Produto,
               que nao sao das quatro rodovias / nao sao fonte desta operacao);
  - BM_GOxxx:  as quatro pastas de entrega final de BM (nota H238), por drive_id:item_id.
Nenhum conteudo e baixado: nome, tamanho, datas, autor, etag e quickXorHash vem dos metadados.

Retomavel (regra 7): fila de pastas em origem_fila; rodar de novo continua a execucao aberta.
Erros de pasta ficam registrados na fila (regra 5) e nao interrompem a varredura.

Uso: python scripts/363_g2_inventario_origem.py [--threads 6] [--nova]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from graph_util import ErroGraph, Graph, explicar  # noqa: E402
from migra_g2.base import (abrir_execucao, agora, cfg, conectar, escrever_csv,  # noqa: E402
                           fechar_execucao, gib, num, saida)
from migra_g2.governador import baixar_prioridade_propria, esperar_folga  # noqa: E402

DRIVE_PROG = "00000000-0000-0000-0000-000000000000"
PASTA_G2 = "3. Desenvolvimento/G2 -  Engenharia"
FORA = {"_Obsoleto", "_Produto"}           # primeiro nivel do G2 que nao e fonte desta operacao
CAMPOS = ("id,name,size,file,folder,parentReference,createdDateTime,lastModifiedDateTime,"
          "lastModifiedBy,eTag,cTag,webUrl")
CAB = ["fonte", "drive_id", "item_id", "caminho", "nome", "tamanho", "criado_em", "modificado_em",
       "modificado_por", "etag", "quick_xor_hash", "web_url"]

SQL = """INSERT INTO origem_item (drive_id, item_id, fonte, pai_id, caminho, nome, eh_pasta, tamanho, criado_em,
  modificado_em, modificado_por, etag, ctag, quick_xor_hash, web_url, inventariado_em, execucao_id)
  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
  ON CONFLICT(drive_id, item_id) DO UPDATE SET fonte=excluded.fonte, pai_id=excluded.pai_id,
  caminho=excluded.caminho, nome=excluded.nome, eh_pasta=excluded.eh_pasta, tamanho=excluded.tamanho,
  criado_em=excluded.criado_em, modificado_em=excluded.modificado_em, modificado_por=excluded.modificado_por,
  etag=excluded.etag, ctag=excluded.ctag, quick_xor_hash=excluded.quick_xor_hash, web_url=excluded.web_url,
  inventariado_em=excluded.inventariado_em, execucao_id=excluded.execucao_id"""


def linha(it: dict, fonte: str, drive: str, caminho: str, ex: int, quando: str) -> tuple:
    arq = it.get("file")
    u = ((it.get("lastModifiedBy") or {}).get("user") or {})
    return (drive, it["id"], fonte, (it.get("parentReference") or {}).get("id"), caminho, it["name"],
            0 if arq else 1, it.get("size"), it.get("createdDateTime"), it.get("lastModifiedDateTime"),
            u.get("displayName") or u.get("email"), it.get("eTag"), it.get("cTag"),
            ((arq or {}).get("hashes") or {}).get("quickXorHash"), it.get("webUrl"), quando, ex)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--nova", action="store_true", help="abre nova execucao mesmo havendo uma aberta")
    a = ap.parse_args()
    c = cfg()
    print("prioridade:", baixar_prioridade_propria())     # o G2 e secundario (29/10/2026)
    con = conectar()
    g = Graph()

    ab = con.execute("SELECT id FROM execucao WHERE script='363_g2_inventario_origem' AND estado!='concluida'"
                     " ORDER BY id DESC").fetchone()
    if ab and not a.nova:
        ex = ab[0]
        con.execute("UPDATE origem_fila SET erro=NULL WHERE execucao_id=? AND listada_em IS NULL", (ex,))
        con.commit()
        print(f"Retomando execucao {ex} (pastas com erro voltam para a fila)")
    else:
        ex = abrir_execucao(con, "363_g2_inventario_origem", vars(a))
        raiz = g.get(f"/drives/{DRIVE_PROG}/root:/{quote(PASTA_G2)}?$select={CAMPOS}")
        con.execute(SQL, linha(raiz, "Programa", DRIVE_PROG, "", ex, agora()))
        fila = [(ex, DRIVE_PROG, raiz["id"], "Programa", "")]
        bm = json.loads((RAIZ / c["eap"]["fontes_bm"]).read_text(encoding="utf-8"))["fontes_bm"]
        for go, f in bm.items():
            fonte = "BM_" + go.replace("-", "")
            it = g.get(f"/drives/{f['drive_id']}/items/{f['item_id']}?$select={CAMPOS}")
            con.execute(SQL, linha(it, fonte, f["drive_id"], "", ex, agora()))
            fila.append((ex, f["drive_id"], f["item_id"], fonte, ""))
        con.executemany("INSERT OR IGNORE INTO origem_fila (execucao_id, drive_id, item_id, fonte, caminho)"
                        " VALUES (?,?,?,?,?)", fila)
        con.commit()

    t0, listadas, erros = time.time(), 0, 0

    def listar(drive, item_id, fonte, caminho):
        url = f"/drives/{drive}/items/{item_id}/children?$top=999&$select={CAMPOS}"
        return list(g.paginar(url))

    with ThreadPoolExecutor(max_workers=a.threads) as pool:
        while True:
            esperar_folga(c.get("recursos"))                 # cede ao projeto prioritario entre lotes
            pend = con.execute("SELECT drive_id, item_id, fonte, caminho FROM origem_fila WHERE execucao_id=?"
                               " AND listada_em IS NULL AND erro IS NULL LIMIT 500", (ex,)).fetchall()
            if not pend:
                break
            futs = {pool.submit(listar, *p): p for p in pend}
            for fu in as_completed(futs):
                drive, item_id, fonte, caminho = futs[fu]
                try:
                    filhos = fu.result()
                except (ErroGraph, OSError) as e:
                    erros += 1
                    msg = explicar(e) if isinstance(e, ErroGraph) else f"{type(e).__name__}: {e}"
                    con.execute("UPDATE origem_fila SET erro=? WHERE execucao_id=? AND drive_id=? AND item_id=?",
                                (msg[:400], ex, drive, item_id))
                    continue
                quando = agora()
                novos = []
                for it in filhos:
                    cam = f"{caminho}/{it['name']}" if caminho else it["name"]
                    if fonte == "Programa" and not caminho and it["name"] in FORA:
                        continue
                    con.execute(SQL, linha(it, fonte, drive, cam, ex, quando))
                    if it.get("folder"):
                        novos.append((ex, drive, it["id"], fonte, cam))
                con.executemany("INSERT OR IGNORE INTO origem_fila (execucao_id, drive_id, item_id, fonte, caminho)"
                                " VALUES (?,?,?,?,?)", novos)
                con.execute("UPDATE origem_fila SET listada_em=? WHERE execucao_id=? AND drive_id=? AND item_id=?",
                            (quando, ex, drive, item_id))
                listadas += 1
            con.commit()
            n = con.execute("SELECT COUNT(*), SUM(tamanho) FROM origem_item WHERE execucao_id=? AND eh_pasta=0",
                            (ex,)).fetchone()
            print(f"  ... {num(listadas)} pastas listadas, {num(n[0])} arquivos, {gib(n[1])} GiB, "
                  f"{erros} erros, {time.time() - t0:.0f} s", flush=True)

    falhas = con.execute("SELECT COUNT(*) FROM origem_fila WHERE execucao_id=? AND erro IS NOT NULL", (ex,)).fetchone()[0]
    out = saida(c) / "INVENTARIO_ORIGEM_G2.csv"
    linhas = con.execute("SELECT fonte, drive_id, item_id, caminho, nome, tamanho, criado_em, modificado_em,"
                         " modificado_por, etag, quick_xor_hash, web_url FROM origem_item"
                         " WHERE execucao_id=? AND eh_pasta=0 ORDER BY fonte, caminho", (ex,))
    total = escrever_csv(out, CAB, linhas)
    por = con.execute("SELECT fonte, COUNT(*), SUM(tamanho) FROM origem_item WHERE execucao_id=? AND eh_pasta=0"
                      " GROUP BY fonte", (ex,)).fetchall()
    for f in por:
        print(f"  {f[0]:10s} {num(f[1]):>8s} arquivos {gib(f[2]):>8s} GiB")
    fechar_execucao(con, ex, "concluida" if not falhas else "erro",
                    {"arquivos": total, "pastas_com_erro": falhas, "por_fonte": [tuple(x) for x in por]})
    print(f"{num(total)} arquivos -> {out}" + (f"  | {falhas} PASTAS COM ERRO (rodar de novo)" if falhas else ""))
    return 0 if not falhas else 2


if __name__ == "__main__":
    raise SystemExit(main())

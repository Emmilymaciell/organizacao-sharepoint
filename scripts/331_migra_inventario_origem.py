"""
331_migra_inventario_origem - inventario da pasta "5. Compartilhado PARCEIRO"
pelo Microsoft Graph, para a frente de migracao ao PARCEIRO INFRA.

SOMENTE LEITURA: so GET de metadados. Nao baixa conteudo, nao toca na
pasta sincronizada do OneDrive, nao escreve no SharePoint.

Por que nao o delta global (20_inventario.py): no SharePoint o delta so
existe na raiz da biblioteca; esta frente precisa de um inventario proprio,
datado e retomavel, so da pasta 5. A listagem e por pasta (/children),
com fila persistida em origem_fila: se cair, `--retomar` continua da
ultima execucao sem relistar o que ja foi listado.

Grava em data/migracao_parceiro.db (origem_item) e em
output/migracao_parceiro/origem_inventario.csv. Itens que existiam numa
varredura anterior e nao aparecem mais ganham `ausente_desde`.

Uso:
    python scripts/331_migra_inventario_origem.py
    python scripts/331_migra_inventario_origem.py --retomar
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from graph_util import ErroGraph, Graph, explicar  # noqa: E402
from migra_parceiro.base import (abrir_execucao, agora, cfg, conectar,  # noqa: E402
                             escrever_csv, fechar_execucao, gib, num, saida)

CAMPOS = ("id,name,size,file,folder,parentReference,createdDateTime,"
          "lastModifiedDateTime,createdBy,lastModifiedBy,eTag,cTag,webUrl")


def quem(bloco: dict | None) -> str:
    u = (bloco or {}).get("user") or {}
    return u.get("displayName") or u.get("email") or ""


def linha_item(it: dict, caminho_rel: str, drive_id: str, ex_id: int, quando: str) -> tuple:
    arq = it.get("file")
    nome = it["name"]
    ext = Path(nome).suffix.lower().lstrip(".") if arq else None
    return (
        it["id"], drive_id, (it.get("parentReference") or {}).get("id"), caminho_rel, nome, ext,
        0 if arq else 1, it.get("size"), (it.get("folder") or {}).get("childCount"),
        it.get("createdDateTime"), it.get("lastModifiedDateTime"),
        quem(it.get("createdBy")), quem(it.get("lastModifiedBy")),
        it.get("eTag"), it.get("cTag"),
        ((arq or {}).get("hashes") or {}).get("quickXorHash"),
        (arq or {}).get("mimeType"), it.get("webUrl"), quando, ex_id,
    )


SQL_ITEM = """
INSERT INTO origem_item (item_id, drive_id, pai_id, caminho_rel, nome, extensao, eh_pasta,
    tamanho, filhos, criado_em, modificado_em, criado_por, modificado_por, etag, ctag,
    quick_xor_hash, mime_type, web_url, inventariado_em, execucao_id, ausente_desde)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)
ON CONFLICT(item_id) DO UPDATE SET
    pai_id=excluded.pai_id, caminho_rel=excluded.caminho_rel, nome=excluded.nome,
    extensao=excluded.extensao, eh_pasta=excluded.eh_pasta, tamanho=excluded.tamanho,
    filhos=excluded.filhos, criado_em=excluded.criado_em, modificado_em=excluded.modificado_em,
    criado_por=excluded.criado_por, modificado_por=excluded.modificado_por, etag=excluded.etag,
    ctag=excluded.ctag, quick_xor_hash=excluded.quick_xor_hash, mime_type=excluded.mime_type,
    web_url=excluded.web_url, inventariado_em=excluded.inventariado_em,
    execucao_id=excluded.execucao_id, ausente_desde=NULL
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--retomar", action="store_true", help="continua a ultima execucao interrompida")
    args = ap.parse_args()

    c = cfg()
    drive_id = c["origem"]["drive_id"]
    pasta = c["origem"]["pasta"]
    threads = int(c["origem"].get("threads_listagem") or 4)

    con = conectar()
    g = Graph()
    trava = threading.Lock()

    ex_id = None
    if args.retomar:
        r = con.execute("SELECT id FROM execucao WHERE script=? AND estado!='concluida' "
                        "ORDER BY id DESC LIMIT 1", (Path(__file__).name,)).fetchone()
        if r:
            ex_id = r["id"]
            con.execute("UPDATE execucao SET estado='andamento' WHERE id=?", (ex_id,))
            print(f"Retomando execucao {ex_id}")
    if ex_id is None:
        ex_id = abrir_execucao(con, Path(__file__).name, {"pasta": pasta, "threads": threads})
        raiz = g.get(f"/drives/{drive_id}/root:/{quote(pasta)}?$select={CAMPOS}")
        con.execute("INSERT OR IGNORE INTO origem_fila (execucao_id, item_id, caminho_rel) "
                    "VALUES (?,?,?)", (ex_id, raiz["id"], ""))
        con.commit()

    inicio = time.time()
    listadas = itens = erros = 0

    def listar(item_id: str, caminho_rel: str):
        url = (f"/drives/{drive_id}/items/{item_id}/children"
               f"?$top=999&$select={CAMPOS}")
        return item_id, caminho_rel, list(g.paginar(url))

    try:
        with ThreadPoolExecutor(max_workers=threads) as pool:
            while True:
                pendentes = con.execute(
                    "SELECT item_id, caminho_rel FROM origem_fila WHERE execucao_id=? "
                    "AND listada_em IS NULL AND erro IS NULL", (ex_id,)).fetchall()
                if not pendentes:
                    break
                futuros = [pool.submit(listar, p["item_id"], p["caminho_rel"]) for p in pendentes]
                for fut in as_completed(futuros):
                    try:
                        pai_id, pai_rel, filhos = fut.result()
                    except ErroGraph as e:
                        erros += 1
                        # Regra 5: a pasta com erro fica registrada, a varredura segue
                        idx = futuros.index(fut)
                        con.execute("UPDATE origem_fila SET erro=? WHERE execucao_id=? AND item_id=?",
                                    (f"{e} | {explicar(e)}"[:500], ex_id, pendentes[idx]["item_id"]))
                        continue
                    quando = agora()
                    with trava:
                        for it in filhos:
                            rel = f"{pai_rel}/{it['name']}" if pai_rel else it["name"]
                            con.execute(SQL_ITEM, linha_item(it, rel, drive_id, ex_id, quando))
                            itens += 1
                            if "folder" in it:
                                con.execute("INSERT OR IGNORE INTO origem_fila "
                                            "(execucao_id, item_id, caminho_rel) VALUES (?,?,?)",
                                            (ex_id, it["id"], rel))
                        con.execute("UPDATE origem_fila SET listada_em=? WHERE execucao_id=? "
                                    "AND item_id=?", (quando, ex_id, pai_id))
                        con.commit()
                    listadas += 1
                    if listadas % 200 == 0:
                        print(f"  {num(listadas)} pastas listadas · {num(itens)} itens · "
                              f"{time.time() - inicio:.0f} s", flush=True)
    except KeyboardInterrupt:
        fechar_execucao(con, ex_id, "interrompida", {"pastas": listadas, "itens": itens})
        print("Interrompido. Rode de novo com --retomar.")
        return 2

    # quem nao foi visto nesta execucao saiu da origem (so marca, nunca apaga)
    if erros == 0:
        con.execute("UPDATE origem_item SET ausente_desde=? WHERE execucao_id!=? "
                    "AND ausente_desde IS NULL", (agora(), ex_id))
    con.commit()

    tot = con.execute("SELECT SUM(eh_pasta=0) arq, SUM(eh_pasta=1) pst, "
                      "SUM(CASE WHEN eh_pasta=0 THEN tamanho END) b, "
                      "SUM(eh_pasta=0 AND quick_xor_hash IS NULL) semhash "
                      "FROM origem_item WHERE ausente_desde IS NULL").fetchone()
    linhas = con.execute(
        "SELECT item_id, caminho_rel, nome, extensao, eh_pasta, tamanho, criado_em, "
        "modificado_em, modificado_por, etag, ctag, quick_xor_hash FROM origem_item "
        "WHERE ausente_desde IS NULL ORDER BY caminho_rel").fetchall()
    escrever_csv(saida(c) / "origem_inventario.csv",
                 ["item_id", "caminho_rel", "nome", "extensao", "eh_pasta", "bytes", "criado_em",
                  "modificado_em", "modificado_por", "etag", "ctag", "quick_xor_hash"],
                 (tuple(r) for r in linhas))
    resumo = {"pastas_listadas": listadas, "erros": erros, "arquivos": tot["arq"],
              "pastas": tot["pst"], "bytes": tot["b"], "sem_hash": tot["semhash"],
              "segundos": round(time.time() - inicio, 1)}
    fechar_execucao(con, ex_id, "concluida" if erros == 0 else "erro", resumo)
    print(f"\nOrigem: {num(tot['arq'])} arquivos · {num(tot['pst'])} pastas · "
          f"{gib(tot['b'])} GiB · sem hash: {num(tot['semhash'])} · erros: {erros} · "
          f"{resumo['segundos']} s")
    return 0 if erros == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

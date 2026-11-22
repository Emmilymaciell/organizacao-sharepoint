"""
362_g2_foto_parceiro - fotografa (SO LEITURA, PROPFIND) as quatro arvores Engenharia do PARCEIRO.

  --tipo baseline  foto inicial IMUTAVEL, antes de qualquer escrita. So pode existir uma.
                   Exporta BASELINE_PARCEIRO_PREEXISTENTE.csv, grava o SHA-256 do CSV no banco e
                   guarda copia somente-leitura em backup/migracao_parceiro_g2/baseline_<data>/.
  --tipo monitor   foto incremental durante a operacao; compara com a anterior e com o baseline
                   e registra alteracoes externas (alteracao_externa) por oc:fileid.
  --tipo final     foto da auditoria final (mesma comparacao).

Identidade de item = oc:fileid (estavel no Nextcloud em renomear/mover). O etag muda quando o
conteudo muda. Itens criados pelo nosso motor sao reconhecidos pelo fileid registrado em plano.
Erros de listagem ficam em parceiro_erro (nada some do catalogo).

Uso: python scripts/362_g2_foto_parceiro.py --tipo baseline|monitor|final [--threads 4]
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2.base import (abrir_execucao, agora, cfg, conectar, escrever_csv, fechar_execucao,  # noqa: E402
                           num, gib, rodovia_de, saida, sha256_arquivo)
from migra_g2.credencial import parceiro  # noqa: E402

CAB = ["caminho", "rodovia", "tipo", "tamanho", "modificado", "etag", "fileid", "checksums",
       "permissoes", "dono_armazenamento", "foto_id", "foto_inicio"]


def fotografar(con, nc, c, tipo: str, threads: int, ex: int) -> int:
    cur = con.execute("INSERT INTO parceiro_foto (tipo, inicio, execucao_id) VALUES (?,?,?)", (tipo, agora(), ex))
    foto = cur.lastrowid
    con.commit()
    trava = threading.Lock()
    tot = {"arq": 0, "pas": 0, "bytes": 0, "erros": 0}

    def gravar(entradas):
        with trava:
            con.executemany(
                "INSERT OR REPLACE INTO parceiro_item (foto_id, caminho, rodovia, eh_pasta, tamanho, modificado, etag,"
                " fileid, checksums, permissoes, dono) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(foto, e.caminho, rodovia_de(e.caminho, c), int(e.eh_pasta), e.tamanho, e.modificado, e.etag,
                  e.fileid, e.checksums, e.permissoes, e.dono) for e in entradas])
            for e in entradas:
                if e.eh_pasta:
                    tot["pas"] += 1
                else:
                    tot["arq"] += 1
                    tot["bytes"] += e.tamanho or 0

    def listar(caminho):
        try:
            r = nc.listar(caminho, "1")
            if r is None:
                raise IOError("404 (pasta sumiu durante a listagem)")
            return r
        except Exception as ex_:  # noqa: BLE001  (regra 5: registra e segue)
            with trava:
                tot["erros"] += 1
                con.execute("INSERT INTO parceiro_erro (foto_id, caminho, erro, em) VALUES (?,?,?,?)",
                            (foto, caminho, f"{type(ex_).__name__}: {str(ex_)[:300]}", agora()))
            return []

    raizes = list(c["destino"]["arvores"].values())
    pend = []
    for r in raizes:
        res = listar(r)
        gravar(res)                      # inclui a propria raiz (Depth 1 devolve ela mesma)
        pend += [e.caminho for e in res if e.eh_pasta and e.caminho != r]
    with ThreadPoolExecutor(max_workers=threads) as pool:
        while pend:
            lote, pend = pend, []
            for pai, res in zip(lote, pool.map(listar, lote)):
                filhos = [e for e in res if e.caminho != pai]
                gravar(filhos)
                pend += [e.caminho for e in filhos if e.eh_pasta]
            con.commit()
            print(f"  ... {num(tot['pas'])} pastas, {num(tot['arq'])} arquivos, {gib(tot['bytes'])} GiB", flush=True)
    con.execute("UPDATE parceiro_foto SET fim=?, arquivos=?, pastas=?, bytes=?, erros=? WHERE id=?",
                (agora(), tot["arq"], tot["pas"], tot["bytes"], tot["erros"], foto))
    con.commit()
    return foto


def exportar(con, foto: int, destino: Path) -> str:
    ini = con.execute("SELECT inicio FROM parceiro_foto WHERE id=?", (foto,)).fetchone()[0]
    linhas = ((r["caminho"], r["rodovia"], "PASTA" if r["eh_pasta"] else "ARQUIVO", r["tamanho"], r["modificado"],
               r["etag"], r["fileid"], r["checksums"], r["permissoes"], r["dono"], foto, ini)
              for r in con.execute("SELECT * FROM parceiro_item WHERE foto_id=? ORDER BY caminho", (foto,)))
    escrever_csv(destino, CAB, linhas)
    h = sha256_arquivo(destino)
    con.execute("UPDATE parceiro_foto SET sha256_csv=? WHERE id=?", (h, foto))
    con.commit()
    return h


def comparar(con, base: int, atual: int, nossos: set[str]) -> dict:
    """Diferencas por fileid entre duas fotos -> alteracao_externa (upsert por fileid/tipo/caminho)."""
    def mapa(f):
        return {r["fileid"]: r for r in con.execute(
            "SELECT * FROM parceiro_item WHERE foto_id=? AND fileid IS NOT NULL", (f,))}
    a, b = mapa(base), mapa(atual)
    quando = con.execute("SELECT inicio FROM parceiro_foto WHERE id=?", (atual,)).fetchone()[0]
    cont: dict[str, int] = {}

    def reg(fid, ra, rb, tipo):
        nosso = int(fid in nossos)
        cont[tipo + ("_NOSSO" if nosso else "")] = cont.get(tipo + ("_NOSSO" if nosso else ""), 0) + 1
        cam = (rb or ra)["caminho"]
        con.execute(
            "INSERT INTO alteracao_externa (fileid, caminho_inicial, caminho_atual, tamanho_inicial, tamanho_atual,"
            " etag_inicial, etag_atual, tipo, primeira_vez, ultima_vez, foto_inicial, foto_atual, nosso)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(fileid, tipo, caminho_atual) DO UPDATE SET ultima_vez=excluded.ultima_vez,"
            " foto_atual=excluded.foto_atual, etag_atual=excluded.etag_atual, tamanho_atual=excluded.tamanho_atual",
            (fid, ra["caminho"] if ra else None, rb["caminho"] if rb else cam,
             ra["tamanho"] if ra else None, rb["tamanho"] if rb else None,
             ra["etag"] if ra else None, rb["etag"] if rb else None, tipo, quando, quando, base, atual, nosso))

    for fid, rb in b.items():
        ra = a.get(fid)
        if ra is None:
            reg(fid, None, rb, "NOVO")
        else:
            if ra["caminho"] != rb["caminho"]:
                reg(fid, ra, rb, "RENOMEADO_OU_MOVIDO")
            if not rb["eh_pasta"] and (ra["etag"] != rb["etag"] or ra["tamanho"] != rb["tamanho"]):
                reg(fid, ra, rb, "MODIFICADO")
    for fid, ra in a.items():
        if fid not in b:
            reg(fid, ra, None, "REMOVIDO_OU_FORA_DA_ARVORE")
    con.commit()
    return cont


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tipo", required=True, choices=["baseline", "monitor", "final"])
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args()
    c = cfg()
    from migra_g2.governador import baixar_prioridade_propria, esperar_folga
    print("prioridade:", baixar_prioridade_propria())     # o G2 e secundario (29/10/2026)
    esperar_folga(c.get("recursos"))
    con = conectar()
    base = con.execute("SELECT id FROM parceiro_foto WHERE tipo='baseline' AND fim IS NOT NULL").fetchone()
    if a.tipo == "baseline" and base:
        print(f"ABORTADO: o baseline ja existe (foto {base[0]}) e e imutavel.")
        return 1
    if a.tipo != "baseline" and not base:
        print("ABORTADO: falta o baseline (rode --tipo baseline antes de qualquer escrita).")
        return 1
    escritas = con.execute("SELECT COUNT(*) FROM envio").fetchone()[0] + \
        con.execute("SELECT COUNT(*) FROM pasta_criada WHERE resultado='CRIADA'").fetchone()[0]
    if a.tipo == "baseline" and escritas:
        print("ABORTADO: ja houve escrita do motor; o baseline tem de ser anterior a ela.")
        return 1

    ex = abrir_execucao(con, "362_g2_foto_parceiro", vars(a))
    nc = parceiro(c)
    print(f"Fotografando as quatro arvores Engenharia ({a.tipo}) ...")
    foto = fotografar(con, nc, c, a.tipo, a.threads, ex)
    f = con.execute("SELECT * FROM parceiro_foto WHERE id=?", (foto,)).fetchone()
    print(f"foto {foto}: {num(f['pastas'])} pastas, {num(f['arquivos'])} arquivos, {gib(f['bytes'])} GiB, "
          f"{f['erros']} erros de listagem")

    out = saida(c)
    if a.tipo == "baseline":
        csv_ = out / "BASELINE_PARCEIRO_PREEXISTENTE.csv"
        h = exportar(con, foto, csv_)
        rec = RAIZ / "backup" / "migracao_parceiro_g2" / f"baseline_{f['inicio'][:19].replace(':', '')}"
        rec.mkdir(parents=True, exist_ok=True)
        shutil.copy2(csv_, rec / csv_.name)
        (rec / "SHA256SUMS.txt").write_text(f"{h} *{csv_.name}\n", encoding="ascii")
        for p in rec.iterdir():
            os.chmod(p, stat.S_IREAD)
        print(f"baseline SHA-256 {h}\n  copia de recuperacao (somente leitura): {rec}")
        resumo = {"foto": foto, "sha256": h}
    else:
        h = exportar(con, foto, out / f"FOTO_PARCEIRO_{foto:03d}_{a.tipo}.csv")
        nossos = {r[0] for r in con.execute("SELECT fileid_destino FROM plano WHERE fileid_destino IS NOT NULL")} | \
                 {r[0] for r in con.execute("SELECT fileid FROM pasta_criada WHERE fileid IS NOT NULL")}
        anterior = con.execute("SELECT MAX(id) FROM parceiro_foto WHERE id<? AND fim IS NOT NULL", (foto,)).fetchone()[0]
        cont = comparar(con, base[0], foto, nossos)
        print(f"mudancas desde o baseline (por fileid): {cont or 'nenhuma'}")
        if anterior and anterior != base[0]:
            print(f"mudancas desde a foto {anterior}: {comparar(con, anterior, foto, nossos) or 'nenhuma'}")
        resumo = {"foto": foto, "sha256": h, "mudancas": cont}
    fechar_execucao(con, ex, "concluida", resumo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

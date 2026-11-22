"""
334_migra_evidencias - le documentos-chave da origem (so leitura, so memoria)
para confirmar data, referencia, obra, orgao e entidade usadas na
classificacao. Autorizado pela Emmily em 19/10/2026 (secao 10.4).

Recebe trechos de caminho (relativos a pasta 5); para cada arquivo que casa,
baixa para a memoria pelo Graph, extrai o texto das primeiras paginas e
registra os achados (numeros de oficio, processo, datas, obras, orgaos) em
output/migracao_parceiro/evidencias.json. O texto integral NAO e gravado;
`--mostrar` imprime um trecho inicial na tela para leitura humana.

Uso:
    python scripts/334_migra_evidencias.py "17_Resposta TCE 06 10 26/Manifestação" --mostrar 2500
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from graph_util import Graph  # noqa: E402
from migra_parceiro.base import agora, cfg, conectar, saida  # noqa: E402
from migra_parceiro.leitura import achados, baixar_memoria, texto  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("trechos", nargs="+", help="trecho do caminho relativo (LIKE %%trecho%%)")
    ap.add_argument("--max", type=int, default=5, help="maximo de arquivos por trecho")
    ap.add_argument("--paginas", type=int, default=3)
    ap.add_argument("--mostrar", type=int, default=0, help="imprime N caracteres do texto")
    ap.add_argument("--catalogo-global", action="store_true",
                    help="procura no catalogo global (organizacao.db), fora da pasta 5: so EVIDENCIA, "
                         "esses arquivos nao entram na migracao")
    args = ap.parse_args()

    c = cfg()
    con = conectar()
    if args.catalogo_global:
        import sqlite3
        con = sqlite3.connect(f"file:{RAIZ / 'data' / 'organizacao.db'}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        con.execute("CREATE TEMP VIEW origem_item AS SELECT item_id, drive_id, caminho AS caminho_rel, "
                    "ltrim(lower(extensao), '.') AS extensao, tamanho, etag, eh_pasta, "
                    "NULL AS ausente_desde FROM item_nuvem "
                    "WHERE deletado=0")
    g = Graph()
    arq_json = saida(c) / "evidencias.json"
    banco = json.loads(arq_json.read_text(encoding="utf-8")) if arq_json.exists() else {}

    for trecho in args.trechos:
        linhas = con.execute(
            "SELECT item_id, drive_id, caminho_rel, extensao, tamanho, etag FROM origem_item "
            "WHERE eh_pasta=0 AND ausente_desde IS NULL AND caminho_rel LIKE ? "
            "AND extensao IN ('pdf','docx') ORDER BY caminho_rel LIMIT ?",
            (f"%{trecho}%", args.max)).fetchall()
        if not linhas:
            print(f"[{trecho}] nenhum PDF/DOCX")
            continue
        for r in linhas:
            print(f"\n=== {r['caminho_rel']}  ({r['tamanho']:,} B)")
            try:
                txt = texto(baixar_memoria(g, r["drive_id"], r["item_id"]), r["extensao"], args.paginas)
            except Exception as e:  # registra e segue (regra 5)
                print(f"  ERRO: {e}")
                banco[r["item_id"]] = {"caminho": r["caminho_rel"], "erro": str(e)[:300], "lido_em": agora()}
                continue
            ach = achados(txt)
            banco[r["item_id"]] = {"caminho": r["caminho_rel"], "etag": r["etag"], "lido_em": agora(),
                                   "paginas": args.paginas, "achados": ach}
            for k, v in ach.items():
                print(f"  {k:13} {v}")
            if args.mostrar:
                print("  --- trecho ---")
                print("  " + " ".join(txt.split())[: args.mostrar])
    arq_json.write_text(json.dumps(banco, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

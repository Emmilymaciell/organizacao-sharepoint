"""
361_g2_acessos - valida, SO LEITURA, os acessos da operacao G2:
  1. Graph: site Programa e biblioteca Programa (GET);
  2. as 4 pastas de entrega final de BM, por drive_id:item_id (GET; o link anonimo NAO e usado);
  3. PARCEIRO: PROPFIND Depth 0 nas quatro raizes Engenharia (existencia, fileid, permissoes).
Nao imprime credencial nem link. Saida 0 = tudo acessivel.

Uso: python scripts/361_g2_acessos.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from graph_util import ErroGraph, Graph, explicar  # noqa: E402
from migra_g2.base import cfg  # noqa: E402
from migra_g2.credencial import parceiro  # noqa: E402


def main() -> int:
    c = cfg()
    falhas = 0
    g = Graph()
    try:
        site = g.site_id()
        libs = {b["name"]: b["id"] for b in g.bibliotecas(site)}
        print(f"[OK] Graph: site Programa; bibliotecas: {', '.join(sorted(libs))}")
    except ErroGraph as e:
        print(f"[FALHA] Graph site: {explicar(e)}")
        return 1

    fontes = json.loads((RAIZ / c["eap"]["fontes_bm"]).read_text(encoding="utf-8"))["fontes_bm"]
    for go, f in fontes.items():
        try:
            it = g.get(f"/drives/{f['drive_id']}/items/{f['item_id']}?$select=id,name,folder,size,lastModifiedDateTime")
            print(f"[OK] BM {go}: pasta {it['name']!r}, {it.get('folder', {}).get('childCount')} filhos, "
                  f"{it.get('size', 0) / 2**30:.2f} GiB, modificada {it.get('lastModifiedDateTime')}")
        except ErroGraph as e:
            falhas += 1
            print(f"[FALHA] BM {go}: {explicar(e)}")

    nc = parceiro(c)
    for go, raiz in c["destino"]["arvores"].items():
        try:
            e = nc.estado(raiz)
            if e is None:
                falhas += 1
                print(f"[FALHA] PARCEIRO {go}: raiz nao existe: {raiz}")
            else:
                print(f"[OK] PARCEIRO {go}: fileid {e.fileid}, permissoes {e.permissoes}, {(e.tamanho or 0) / 2**30:.2f} GiB")
        except Exception as ex:  # noqa: BLE001
            falhas += 1
            print(f"[FALHA] PARCEIRO {go}: {type(ex).__name__}: {str(ex)[:200]}")
    print("TUDO CERTO" if not falhas else f"{falhas} FALHA(S)")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())

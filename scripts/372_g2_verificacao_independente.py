"""
372_g2_verificacao_independente - conferencia por METODO DIFERENTE do motor, SO LEITURA.

O motor validou cada arquivo pela releitura logo apos o envio. Aqui a cadeia e outra: parte da arvore REAL
do PARCEIRO (PROPFIND agora, nao a foto do banco), sorteia uma amostra estratificada por categoria, relê cada
arquivo inteiro do PARCEIRO, calcula o QuickXor e compara com o quickXorHash que o Graph informa AGORA para o
item de origem (GET por item_id). Divergencia ou ausencia = FAIL.

Uso: python scripts/372_g2_verificacao_independente.py --rodovia RD-103 [--por-categoria 8] [--max-mib 64]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from graph_util import Graph  # noqa: E402
from migra_g2.base import agora, cfg, conectar, saida  # noqa: E402
from migra_g2.credencial import parceiro  # noqa: E402
from migra_parceiro.motor import _mesmo_qxh  # noqa: E402
from migra_parceiro.quickxor import QuickXorHash  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rodovia", default="RD-103")
    ap.add_argument("--por-categoria", type=int, default=8)
    ap.add_argument("--max-mib", type=int, default=64)
    ap.add_argument("--semente", type=int, default=20261028)
    a = ap.parse_args()
    c = cfg()
    con = conectar()
    nc, g = parceiro(c), Graph()
    rng = random.Random(a.semente)
    linhas = [dict(r) for r in con.execute(
        "SELECT p.categoria, p.destino_rel, p.drive_id, p.item_id, o.tamanho FROM plano p JOIN origem_item o "
        "USING (drive_id, item_id) WHERE p.rodovia=? AND p.estado='VALIDADO' AND o.tamanho <= ?",
        (a.rodovia, a.max_mib * 2**20))]
    por = {}
    for ln in linhas:
        por.setdefault(ln["categoria"], []).append(ln)
    amostra = [x for cat in sorted(por) for x in rng.sample(por[cat], min(a.por_categoria, len(por[cat])))]
    res, ok = [], 0
    for x in amostra:
        e = nc.estado(x["destino_rel"])                       # existencia real agora
        it = g.get(f"/drives/{x['drive_id']}/items/{x['item_id']}?$select=size,file")
        qx_ms = ((it.get("file") or {}).get("hashes") or {}).get("quickXorHash")
        q, n = QuickXorHash(), 0
        if e:
            for b in nc.ler_stream(x["destino_rel"]):
                q.update(b)
                n += len(b)
        passou = bool(e) and n == it.get("size") and _mesmo_qxh(q.base64(), qx_ms)
        ok += passou
        res.append({"categoria": x["categoria"], "destino": x["destino_rel"], "existe": bool(e), "bytes_lidos": n,
                    "tamanho_origem_agora": it.get("size"), "qx_parceiro": q.base64(), "qx_microsoft_agora": qx_ms,
                    "resultado": "PASS" if passou else "FAIL"})
        print(f"[{'PASS' if passou else 'FAIL'}] {x['categoria']:13s} {x['destino_rel'][-90:]}", flush=True)
    out = saida(c) / f"auditoria_{a.rodovia}" / "06_VERIFICACAO_INDEPENDENTE_AMOSTRA.json"
    out.write_text(json.dumps({"quando": agora(), "metodo": __doc__.strip().splitlines()[2:6], "semente": a.semente,
                               "universo_validados_ate_max_mib": len(linhas), "amostra": len(amostra), "pass": ok,
                               "fail": len(amostra) - ok, "itens": res}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{ok}/{len(amostra)} PASS -> {out}")
    return 0 if ok == len(amostra) else 1


if __name__ == "__main__":
    raise SystemExit(main())

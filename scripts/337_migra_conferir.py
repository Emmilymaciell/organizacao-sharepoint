"""
337_migra_conferir - conferencia de 100% dos arquivos COPIADO de um lote.

Le cada arquivo DO PARCEIRO (bytes persistidos no servidor, via rclone --rc-serve,
sem cache de disco), calcula QuickXorHash e SHA-256 na mesma leitura e:
  - com hash do servidor na origem: QuickXor do destino == quickXorHash da
    Microsoft (mesmo algoritmo) e mesmo tamanho -> VALIDADO;
  - sem hash do servidor (52 arquivos): le tambem a origem e compara SHA-256.
Depois confere se a origem ainda e a do inventario (ORIGEM_ALTERADA se nao).

Somente leitura nos dois lados; roda em qualquer fase exceto 'diagnostico'
(precisa do rcd). Resultado por arquivo em verificacao (banco).

Uso:
    python scripts/337_migra_conferir.py PILOTO-01
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_parceiro.base import abrir_execucao, cfg, conectar, fechar_execucao  # noqa: E402
from migra_parceiro.motor import Motor, Trava  # noqa: E402
from migra_parceiro.rc import Rcd  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("lote")
    ap.add_argument("--concorrencia", type=int, default=4)
    a = ap.parse_args()
    c = cfg()
    con = conectar()
    ex_id = abrir_execucao(con, Path(__file__).name, {"lote": a.lote})
    flags = [str(f) for f in (c["transferencia"].get("flags_rclone") or [])]
    with Trava(con), Rcd(flags=flags, log=RAIZ / "logs" / f"rclone_conferir_{a.lote}.log") as rcd:
        r = Motor(con, rcd, c["remotes"]["origem"], c["remotes"]["destino"], concorrencia=a.concorrencia,
                  execucao_id=ex_id).conferir(a.lote)
    fechar_execucao(con, ex_id, "concluida", r)
    print(r)
    ruins = sum(v for k, v in r.items() if k in ("DIVERGENTE", "AUSENTE", "ORIGEM_ALTERADA", "ERRO"))
    return 0 if not ruins else 2


if __name__ == "__main__":
    raise SystemExit(main())

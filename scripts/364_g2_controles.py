"""
364_g2_controles - baixa (download ISOLADO, regra 2) as planilhas de controle do G2 para
tmp/migracao_parceiro_g2/controles/, com SHA-256 e item_id/etag registrados em controles.json:
  - 00_ Lista mestre.xlsx
  - 03 - Acompanhamento de Projetos Executivo.xlsx
  - 02_Acompanhamento de NC automatizada.xlsm
Sao a evidencia de entrega/aprovacao da classificacao. So GET.

Uso: python scripts/364_g2_controles.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import quote

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from graph_util import Graph  # noqa: E402
from migra_g2.base import agora, cfg, pasta  # noqa: E402
from migra_parceiro.leitura import baixar_memoria  # noqa: E402

DRIVE_PROG = "00000000-0000-0000-0000-000000000000"
G2 = "3. Desenvolvimento/G2 -  Engenharia"
ARQUIVOS = ["00_ Lista mestre.xlsx", "03 - Acompanhamento de Projetos Executivo.xlsx",
            "02_Acompanhamento de NC automatizada.xlsm"]


def main() -> int:
    g = Graph()
    dest = pasta("tmp", cfg()) / "controles"
    dest.mkdir(parents=True, exist_ok=True)
    reg = []
    for nome in ARQUIVOS:
        it = g.get(f"/drives/{DRIVE_PROG}/root:/{quote(G2 + '/' + nome)}"
                   "?$select=id,name,size,eTag,lastModifiedDateTime,lastModifiedBy,file")
        dados = baixar_memoria(g, DRIVE_PROG, it["id"], teto=64 * 2**20)
        (dest / nome).write_bytes(dados)
        reg.append({"nome": nome, "item_id": it["id"], "etag": it["eTag"], "tamanho": it["size"],
                    "modificado": it["lastModifiedDateTime"],
                    "modificado_por": ((it.get("lastModifiedBy") or {}).get("user") or {}).get("displayName"),
                    "quick_xor": ((it.get("file") or {}).get("hashes") or {}).get("quickXorHash"),
                    "sha256": hashlib.sha256(dados).hexdigest(), "baixado_em": agora()})
        print(f"{nome}: {len(dados):,} B, modificado {it['lastModifiedDateTime']}")
    (dest / "controles.json").write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
383_g2_inventario_parcial - atualiza, SO LEITURA (Graph GET), o inventario da origem em subpastas do G2, sem refazer
o inventario completo (363). Motivo (30/10/2026): a Emmily informou revisoes novas no SharePoint (ex.: RD-103
Desapropriacao "REV 03 - 27 10 26") posteriores ao inventario da execucao 363 em vigor.

Os itens entram (upsert) na ULTIMA execucao concluida do 363, que e a que o planejador (366) usa. Nenhum conteudo e
baixado. Uso: python scripts/383_g2_inventario_parcial.py "RD103/4_Projeto Executivo" ["RD102A/4_Projeto Executivo" ...]
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from urllib.parse import quote

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from graph_util import Graph  # noqa: E402
from migra_g2.base import agora, conectar, num  # noqa: E402
from migra_g2.governador import baixar_prioridade_propria, esperar_folga  # noqa: E402

_s = importlib.util.spec_from_file_location("inv", RAIZ / "scripts" / "363_g2_inventario_origem.py")
inv = importlib.util.module_from_spec(_s)
_s.loader.exec_module(inv)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    alvos = sys.argv[1:]
    if not alvos:
        print(__doc__)
        return 1
    baixar_prioridade_propria()
    con = conectar()
    con.execute("PRAGMA busy_timeout = 900000")
    ex = con.execute("SELECT MAX(id) FROM execucao WHERE script='363_g2_inventario_origem' AND estado='concluida'"
                     ).fetchone()[0]
    g = Graph()
    for alvo in alvos:
        antes = con.execute("SELECT COUNT(*) FROM origem_item WHERE fonte='Programa' AND eh_pasta=0 AND caminho LIKE ?",
                            (alvo + "/%",)).fetchone()[0]
        raiz = g.get(f"/drives/{inv.DRIVE_PROG}/root:/{quote(inv.PASTA_G2 + '/' + alvo)}?$select={inv.CAMPOS}")
        fila, n_arq, novos = [(raiz["id"], alvo)], 0, 0
        while fila:
            esperar_folga()
            item_id, cam = fila.pop()
            quando = agora()
            for it in g.paginar(f"/drives/{inv.DRIVE_PROG}/items/{item_id}/children?$top=999&$select={inv.CAMPOS}"):
                c2 = f"{cam}/{it['name']}"
                ja = con.execute("SELECT 1 FROM origem_item WHERE drive_id=? AND item_id=?",
                                 (inv.DRIVE_PROG, it["id"])).fetchone()
                con.execute(inv.SQL, inv.linha(it, "Programa", inv.DRIVE_PROG, c2, ex, quando))
                if it.get("folder"):
                    fila.append((it["id"], c2))
                else:
                    n_arq += 1
                    novos += 0 if ja else 1
            con.commit()
        print(f"{alvo}: {num(n_arq)} arquivos (antes {num(antes)}; novos {num(novos)}) -> execucao {ex}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

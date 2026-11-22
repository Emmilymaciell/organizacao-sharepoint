"""
carregar_inventario_ficticio - monta data/organizacao.db a partir de
examples/inventario_ficticio.csv, sem rede e sem credencial.

Substitui a etapa E1 (scripts/20_inventario.py, que le o SharePoint pelo
Microsoft Graph) para quem quer ver as etapas seguintes rodando com dados
inventados. Biblioteca, site e IDs sao ficticios (contoso.sharepoint.com).

Uso (na raiz do repositorio):
    python examples/carregar_inventario_ficticio.py
    python scripts/30_duplicatas.py --min-bytes 0
"""

from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
CSV = RAIZ / "examples" / "inventario_ficticio.csv"
BANCO = RAIZ / "data" / "organizacao.db"
ESQUEMA = RAIZ / "sql" / "01_esquema.sql"

DRIVE_ID = "00000000-0000-0000-0000-000000000000"
BIBLIOTECA = "Programa"
WEB = "https://contoso.sharepoint.com/sites/exemplo/Programa"


def main() -> None:
    BANCO.parent.mkdir(parents=True, exist_ok=True)
    if BANCO.exists():
        BANCO.unlink()
    con = sqlite3.connect(BANCO)
    con.executescript(ESQUEMA.read_text(encoding="utf-8"))
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with CSV.open(encoding="utf-8") as fh:
        linhas = list(csv.DictReader(fh))
    ids = {r["caminho"]: r["item_id"] for r in linhas}
    total = 0
    for r in linhas:
        cam = r["caminho"]
        partes = cam.split("/")
        pai = "/".join(partes[:-1])
        nome = partes[-1]
        eh_pasta = int(r["eh_pasta"])
        ext = "" if eh_pasta or "." not in nome else "." + nome.rsplit(".", 1)[1].lower()
        con.execute(
            """INSERT INTO item_nuvem (drive_id, item_id, biblioteca, pai_id, caminho, pasta_pai, nome,
                   extensao, eh_pasta, nivel1, nivel2, profundidade, tamanho, criado_em, modificado_em,
                   criado_por, criado_por_email, modificado_por, quick_xor_hash, web_url, visto_em)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (DRIVE_ID, r["item_id"], BIBLIOTECA, ids.get(pai), cam, pai, nome, ext, eh_pasta,
             partes[0], partes[1] if len(partes) > 1 else None, len(partes), int(r["tamanho"]),
             r["criado_em"], r["modificado_em"], r["criado_por"], "pessoa@example.com", r["criado_por"],
             r["quick_xor_hash"] or None, f"{WEB}/{cam}", agora))
        total += int(r["tamanho"])
    con.execute(
        "INSERT INTO biblioteca (drive_id, nome, tipo, web_url, quota_usada, quota_total, quota_lixeira,"
        " raiz_tamanho, raiz_filhos, visto_em) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (DRIVE_ID, BIBLIOTECA, "documentLibrary", WEB, total, 1024 ** 4, 0, total, 4, agora))
    con.execute(
        "INSERT INTO varredura (drive_id, biblioteca, estado, itens, paginas, inicio, fim)"
        " VALUES (?,?,?,?,?,?,?)", (DRIVE_ID, BIBLIOTECA, "concluida", len(linhas), 1, agora, agora))
    con.commit()
    con.close()
    print(f"{len(linhas)} itens ficticios gravados em {BANCO.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()

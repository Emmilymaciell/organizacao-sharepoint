"""
360_g2_eap - confere o pacote da EAP G2 contra o Excel ORIGINAL e grava os 156 nos no banco G2.

Confere, por programa:
  - SHA-256 do Excel (privado/, fora do Git) igual ao declarado;
  - 11 comentarios na aba Fluxograma, nas celulas esperadas, com texto identico ao manifesto;
  - 156 nos (39 por GO); rotulo e descricao de cada no iguais a linha citada da aba "EAP de Pastas".
O Excel e aberto so para leitura. Qualquer divergencia: sai 1 e nada e gravado.

Uso: python scripts/360_g2_eap.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

import openpyxl  # noqa: E402

from migra_g2.base import abrir_execucao, cfg, conectar, fechar_execucao, sha256_arquivo  # noqa: E402


def main() -> int:
    c = cfg()
    e = c["eap"]
    xlsx, man = RAIZ / e["excel"], RAIZ / e["manifesto"]
    erros: list[str] = []

    sha = sha256_arquivo(xlsx)
    if sha != e["sha256_excel"]:
        erros.append(f"SHA-256 do Excel diferente: {sha}")
    m = json.loads(man.read_text(encoding="utf-8"))
    if m.get("sha256") != e["sha256_excel"]:
        erros.append("o manifesto cita outro SHA-256 de Excel")

    wb = openpyxl.load_workbook(xlsx, read_only=False)
    flux, tab = wb["Fluxograma"], wb["EAP de Pastas"]
    comentarios = {cel.coordinate: cel.comment.text for linha in flux.iter_rows() for cel in linha if cel.comment}
    notas = {n["cell"]: n["text"] for n in m["notes"]}
    if len(comentarios) != 11:
        erros.append(f"{len(comentarios)} comentarios no Fluxograma (esperado 11)")
    for cel, txt in notas.items():
        if comentarios.get(cel, "").strip() != txt.strip():
            erros.append(f"nota {cel}: texto difere do Excel")

    nos = [n for arv in m["road_trees"].values() for n in arv]
    por_go = Counter(n["road"] for n in nos)
    if len(nos) != 156 or set(por_go.values()) != {39}:
        erros.append(f"nos: {len(nos)} {dict(por_go)}")
    for n in nos:
        rot, desc = tab.cell(n["sheet_row"], 2).value, tab.cell(n["sheet_row"], 3).value
        if (rot or "").strip() != n["label"].strip() or (desc or "").strip() != (n["description"] or "").strip():
            erros.append(f"no linha {n['sheet_row']}: {n['label']!r} != Excel {rot!r}")
        if n["has_note"] and n["flux_cell"] not in comentarios:
            erros.append(f"no {n['relative_parceiro_path']} marcado com nota, mas {n['flux_cell']} nao tem comentario")

    print(f"Excel SHA-256 {sha[:12]}... | comentarios {len(comentarios)} | nos {len(nos)} {dict(por_go)}")
    if erros:
        print("DIVERGENCIAS:\n  " + "\n  ".join(erros[:50]))
        return 1

    con = conectar()
    ex = abrir_execucao(con, "360_g2_eap", {"sha256": sha})
    with con:
        con.execute("DELETE FROM eap_no")
        con.executemany(
            "INSERT INTO eap_no (caminho, rodovia, nivel, rotulo, descricao, linha_eap, celula_flux, tem_nota)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [(n["relative_parceiro_path"], n["road"], n["level"], n["label"], n["description"],
              n["sheet_row"], n["flux_cell"], int(n["has_note"])) for n in nos])
    fechar_execucao(con, ex, "concluida", {"nos": len(nos), "notas": len(comentarios)})
    print("OK: pacote consistente; 156 nos gravados em eap_no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

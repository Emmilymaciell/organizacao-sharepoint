"""
374_md_para_docx - converte relatorios Markdown simples (titulos, listas, tabelas, negrito) em DOCX para
apresentacao a coordenacao. Le e grava so arquivos locais.

Uso: python scripts/374_md_para_docx.py <arquivo.md> [<arquivo.md> ...]   (gera <arquivo>.docx ao lado)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.shared import Pt


def runs(par, texto: str):
    for i, parte in enumerate(re.split(r"\*\*", texto)):
        r = par.add_run(parte.replace("`", ""))
        r.bold = i % 2 == 1


def converter(md: Path) -> Path:
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10)
    linhas = md.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(linhas):
        ln = linhas[i]
        if ln.startswith("|"):
            bloco = []
            while i < len(linhas) and linhas[i].startswith("|"):
                if not re.match(r"^\|[\s:|-]+\|$", linhas[i]):
                    bloco.append([c.strip() for c in linhas[i].strip("|").split("|")])
                i += 1
            t = doc.add_table(rows=len(bloco), cols=max(len(b) for b in bloco))
            t.style = "Light Grid Accent 1"
            for r_, cel in enumerate(bloco):
                for c_, txt in enumerate(cel):
                    runs(t.cell(r_, c_).paragraphs[0], txt.replace("&nbsp;", " "))
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", ln)
        if m:
            doc.add_heading(m.group(2).replace("`", ""), level=len(m.group(1)))
        elif re.match(r"^\s*[-*]\s+", ln):
            runs(doc.add_paragraph(style="List Bullet"), re.sub(r"^\s*[-*]\s+", "", ln))
        elif ln.strip():
            runs(doc.add_paragraph(), ln)
        i += 1
    destino = md.with_suffix(".docx")
    doc.save(destino)
    return destino


if __name__ == "__main__":
    for a in sys.argv[1:]:
        print(converter(Path(a)))

"""
380_g2_word_com_pdf - arquivos Word editaveis nas arvores 04. Engenharia das 4 rodovias. Instrucao da Emmily,
30/10/2026 (~14:30): "NAO DEIXE NENHUM WORD EDITAVEL SE TIVER PDF, A NAO SER BM, MAS AI VC ME FALA QUAIS SE TIVER
WORD E SE TEM VERSAO PDF, E ... OS WORDS Q N TIVER PDF VC FAZ UMA LISTA DELES COM OS CAMINHOS PRA EU VER SE MANTEM".

Word = .doc .docx .docm .dot .dotx .rtf .odt. "Tem PDF" = PDF com o MESMO NOME-BASE na MESMA PASTA (comparacao sem
acento/caixa de extensao). PDF de mesmo nome em OUTRA pasta so e informado (nao autoriza exclusao).
  - fora do BM, com PDF na mesma pasta  -> EXCLUIR (com --executar: PROPFIND confirma fileid/etag do Word e que o PDF
    continua la; DELETE com If-Match: <etag>; vai para a lixeira do Nextcloud)
  - dentro de 02. Boletim de Medicao   -> so listado (com ou sem PDF)
  - sem PDF                            -> so listado, para decisao
Eventograma nunca e tocado (nota 11). Base: a foto mais recente do PARCEIRO (362).

Saida: output/migracao_parceiro_g2/WORD_EDITAVEIS_G2.xlsx (abas EXCLUIDOS_COM_PDF, BM, SEM_PDF) + CSV; tabela exclusao.
Uso:   python scripts/380_g2_word_com_pdf.py [--executar] [--foto N]
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2.base import agora, cfg, conectar, escrever_csv, num, saida  # noqa: E402
from migra_g2.trava import exigir_fase_execucao  # noqa: E402

WORD = {".doc", ".docx", ".docm", ".dot", ".dotx", ".rtf", ".odt"}
CAB = ["rodovia", "acao", "word_caminho", "word_tamanho", "word_modificado", "pdf_mesma_pasta",
       "pdf_mesmo_nome_outra_pasta", "fileid", "etag", "resultado", "http", "em"]


def nfc(s):
    return unicodedata.normalize("NFC", s or "")


def chave(nome: str) -> str:
    base = unicodedata.normalize("NFKD", PurePosixPath(nome).stem).encode("ascii", "ignore").decode().lower()
    return " ".join(base.split())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--executar", action="store_true")
    ap.add_argument("--foto", type=int)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    c = cfg()
    if a.executar:
        exigir_fase_execucao(c, '380_g2_word_com_pdf')   # PD-18
    con = conectar()
    con.execute("PRAGMA busy_timeout = 900000")
    foto = a.foto or con.execute("SELECT MAX(id) FROM parceiro_foto WHERE fim IS NOT NULL").fetchone()[0]
    arvores = {go: nfc(r) for go, r in c["destino"]["arvores"].items()}
    words, pdf_pasta, pdf_nome = [], set(), defaultdict(list)
    for cam, tam, mod, fid, et in con.execute("SELECT caminho, tamanho, modificado, fileid, etag FROM parceiro_item WHERE"
                                              " foto_id=? AND eh_pasta=0", (foto,)):
        cam = nfc(cam)
        go = next((g for g, r in arvores.items() if cam.startswith(r + "/")), None)
        if not go:
            continue
        p = PurePosixPath(cam)
        ext = p.suffix.lower()
        if ext == ".pdf":
            pdf_pasta.add((str(p.parent), chave(p.name)))
            pdf_nome[(go, chave(p.name))].append(cam)
        elif ext in WORD:
            words.append((go, cam, tam, mod, fid, et))
    linhas = []
    for go, cam, tam, mod, fid, et in sorted(words):
        p = PurePosixPath(cam)
        rel = cam[len(arvores[go]) + 1:]
        mesma = (str(p.parent), chave(p.name)) in pdf_pasta
        outras = [x for x in pdf_nome.get((go, chave(p.name)), []) if str(PurePosixPath(x).parent) != str(p.parent)]
        if rel.startswith("09. Eventograma"):
            acao = "EVENTOGRAMA_NAO_TOCADO"
        elif rel.startswith("02. Boletim de Medição"):
            acao = "BM_SO_LISTAR"
        elif mesma:
            acao = "EXCLUIR"
        else:
            acao = "SEM_PDF_DECIDIR"
        pdf = f"{p.parent}/{p.stem}.pdf" if mesma else ""
        linhas.append([go, acao, cam, tam, mod, pdf, " | ".join(outras[:3]), fid, et, "LISTADO", None, agora()])
    print(f"foto {foto}: {num(len(linhas))} Word |", dict(Counter((l[0], l[1]) for l in linhas)))

    if a.executar:
        from migra_g2.credencial import parceiro
        from migra_g2.governador import baixar_prioridade_propria
        baixar_prioridade_propria()
        nc = parceiro(c)
        for l in linhas:
            if l[1] != "EXCLUIR":
                continue
            e = nc.estado(l[2])
            if e is None or e.eh_pasta or e.fileid != l[7] or (e.etag or "") != (l[8] or ""):
                l[9] = "PRESERVADO_ALTERADO"
                continue
            par = PurePosixPath(l[2]).parent
            irmaos = nc.listar(str(par), "1") or []
            if not any(not x.eh_pasta and x.caminho.lower().endswith(".pdf") and
                       chave(PurePosixPath(x.caminho).name) == chave(PurePosixPath(l[2]).name) for x in irmaos):
                l[9] = "PRESERVADO_PDF_SUMIU"
                continue
            st, _ = nc._req_ret("DELETE", nc.url_arquivo(l[2]), {"If-Match": f'"{e.etag}"'})
            ok = st in (200, 204) and nc.estado(l[2]) is None
            l[9], l[10], l[11] = ("EXCLUIDO" if ok else "NAO_EXCLUIDO"), st, agora()
            con.execute("INSERT INTO exclusao VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (l[11], l[0], "WORD_COM_PDF", "", l[2], l[7], l[8], l[3], None, l[9], st,
                         f"Word com PDF de mesmo nome na mesma pasta ({l[5]}); instrucao da Emmily 30/10/2026"))
            if ok:      # se fomos nos que enviamos, o plano registra que saiu do PARCEIRO
                con.execute("UPDATE plano SET estado='EXCLUIDO_WORD_COM_PDF', erro=?, atualizado_em=? WHERE "
                            "fileid_destino=?", ("Word excluido: ha PDF de mesmo nome na mesma pasta", agora(), l[7]))
            con.commit()
        print(dict(Counter((l[1], l[9]) for l in linhas if l[1] == "EXCLUIR")))

    out = saida(c)
    sufixo = "" if a.executar else "_SIMULACAO"
    escrever_csv(out / f"WORD_EDITAVEIS_G2{sufixo}.csv", CAB, linhas)
    wb = Workbook()
    abas = [("EXCLUIDOS_COM_PDF", "EXCLUIR", "Word fora do BM com PDF de mesmo nome na mesma pasta"
             + (" — excluídos (lixeira do Nextcloud)" if a.executar else " — SIMULAÇÃO, nada excluído")),
            ("BM", "BM_SO_LISTAR", "Word dentro de 02. Boletim de Medição — NÃO excluídos; coluna 'PDF na mesma pasta'"),
            ("SEM_PDF", "SEM_PDF_DECIDIR", "Word sem PDF na mesma pasta — decidir se mantém (coluna 'Decisão')")]
    cab = ["Rodovia", "Caminho do Word no PARCEIRO", "Tamanho (KB)", "Modificado", "PDF na mesma pasta",
           "PDF de mesmo nome em outra pasta", "Resultado", "Decisão (MANTER / EXCLUIR)"]
    for i, (aba, acao, titulo) in enumerate(abas):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = aba
        ws["A1"] = titulo
        ws["A1"].font = Font(name="Arial", bold=True, size=12)
        for j, h in enumerate(cab, 1):
            x = ws.cell(3, j, h)
            x.font = Font(name="Arial", bold=True, color="FFFFFF")
            x.fill = PatternFill("solid", fgColor="1F3864")
        for k, w in enumerate([10, 110, 11, 28, 60, 60, 20, 22], 1):
            ws.column_dimensions[chr(64 + k)].width = w
        r = 4
        for l in linhas:
            if l[1] != acao:
                continue
            vals = [l[0], l[2], round((l[3] or 0) / 1024, 1), l[4], l[5] or ("SIM" if acao != "SEM_PDF_DECIDIR" and l[5]
                    else "NÃO"), l[6], l[9], ""]
            for j, v in enumerate(vals, 1):
                ws.cell(r, j, v).font = Font(name="Arial", size=10)
                ws.cell(r, j).alignment = Alignment(vertical="top")
            r += 1
        ws.freeze_panes = "A4"
        ws.auto_filter.ref = f"A3:H{max(r - 1, 3)}"
    p = out / f"WORD_EDITAVEIS_G2{sufixo}.xlsx"
    wb.save(p)
    print(f"-> {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
378_g2_excel_entregaveis - gera, SO LEITURA (banco + manifesto + disco), as duas planilhas de entrega da G2:

  output/migracao_parceiro_g2/EAP_G2_NOVA_ARQUITETURA.xlsx
      a arquitetura da EAP 04. Engenharia como ficou no PARCEIRO apos a migracao G2: modelo da EAP (39 nos) com a
      regra aplicada de cada uma das 11 notas, a estrutura real de pastas das 4 rodovias (foto final do PARCEIRO),
      quantos arquivos ha em cada no e de quem sao (enviados pela migracao, preexistentes, de terceiros depois).
  output/migracao_parceiro_g2/INDICE_ENTREGAVEIS_G2.xlsx
      indice de todos os relatorios, matrizes, auditorias, fotos, testes, documentos e backups da operacao G2.

Contagens e totais na planilha sao FORMULAS (SUMIFS sobre a aba PASTAS_PARCEIRO); os dados por pasta vem da foto
mais recente do PARCEIRO (362). Nenhuma escrita no PARCEIRO nem no SharePoint.
Uso: python scripts/378_g2_excel_entregaveis.py [--foto N] [--foto-base 13]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2.base import cfg, conectar, saida  # noqa: E402

FONTE = "Arial"
AZUL = "1F3864"
F_TIT = Font(name=FONTE, size=14, bold=True, color=AZUL)
F_CAB = Font(name=FONTE, size=10, bold=True, color="FFFFFF")
F_TXT = Font(name=FONTE, size=10)
F_NEG = Font(name=FONTE, size=10, bold=True)
F_LINK = Font(name=FONTE, size=10, color="0563C1", underline="single")
P_CAB = PatternFill("solid", fgColor=AZUL)
P_N1 = PatternFill("solid", fgColor="D9E1F2")
P_BLOQ = PatternFill("solid", fgColor="FCE4D6")
P_SOPASTA = PatternFill("solid", fgColor="FFF2CC")
FINO = Side(style="thin", color="BFBFBF")
BORDA = Border(left=FINO, right=FINO, top=FINO, bottom=FINO)
GIB = 1024 ** 3
RODOVIAS = ["RD-101", "RD-102A", "RD-103", "RD-104"]

# regra aplicada por ramo (docs/migracao_parceiro/g2/G2_EAP_04_ENGENHARIA_REGRAS_E_ANOTACOES.md, interpretacao
# operacional) + decisoes da operacao (D-12, corte de BM de 29/10/2026)
REGRA = {
    "01. Projetos/01. Anteprojeto": "Material pertinente de cada rodovia, com suborganizacao tecnica coerente nas 4 rodovias.",
    "01. Projetos/02. Executivo": "Somente revisoes FINAIS APROVADAS, sem ressalva, por disciplina: 01. FASE DE ESTUDOS, "
                                  "02. VIARIO PRINCIPAL, 03. VIARIO SECUNDARIO, 04. OAE e 05. VOLUMES FINAIS.",
    "01. Projetos/03. RPFO (Revisão de Projeto em Fase de Obra)": "So a pasta da EAP; nenhum documento nesta frente.",
    "02. Boletim de Medição": "Origem somente as 4 pastas especiais de BM (nota H238); documentacao da supervisora nao "
                              "exigida. Corte por mes de referencia: RD-101 e RD-102A ate 05/2026; RD-103 e RD-104 ate "
                              "06/2026 (posteriores excluidos do PARCEIRO em 29/10/2026).",
    "03. Licenciamento Ambiental": "Somente documentos oficialmente entregues/formalizados conforme Lista Mestre.",
    "04. Desapropriação/01. DUP": "So a pasta da EAP; nenhum documento novo.",
    "04. Desapropriação/02. Fase Executória": "Somente RD-102A, CTD 19, 43 e 45, ultimo envio. Nenhum CTD das outras rodovias.",
    "04. Desapropriação": "DUP sem documentos; Fase Executoria so RD-102A (CTD 19/43/45, ultimo envio).",
    "05. Diário de Obras": "Diarios entregues conforme Lista Mestre, organizados por mes.",
    "06. Relatório de Obras": "Somente os oficialmente entregues: Semanal por periodo e numero; Mensal por mes.",
    "07. Não Conformidades": "Cruzamento da planilha de acompanhamento de NC com o acervo; por mes e pasta "
                             "NC-nnn - AAAA-MM-DD - Assunto.",
    "08. Segurança do Trabalho": "Tudo o que foi entregue, por mes, em Inspecao de Alojamento ou Documentacao SST.",
    "09. Eventograma": "BLOQUEADO: nada criado, transferido, renomeado ou alterado (nota 11).",
}
NOTA_N = {"K229": 1, "K232": 2, "K235": 3, "H238": 4, "H256": 5, "H271": 6, "H283": 7, "H289": 8, "H304": 9,
          "H313": 10, "H325": 11}


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def regra_de(rel: str) -> str:
    melhor = ""
    for k in REGRA:
        if (rel == k or rel.startswith(k + "/")) and len(k) > len(melhor):
            melhor = k
    return REGRA.get(melhor, "")


def cabecalho(ws, linha: int, titulos: list[str], larguras: list[int]):
    for i, (t, w) in enumerate(zip(titulos, larguras), 1):
        c = ws.cell(linha, i, t)
        c.font, c.fill, c.border = F_CAB, P_CAB, BORDA
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = ws.cell(linha + 1, 1)
    ws.auto_filter.ref = f"A{linha}:{get_column_letter(len(titulos))}{linha}"


def celula(ws, l, c, v, fonte=F_TXT, fmt=None, quebra=False, recuo=0):
    x = ws.cell(l, c, v)
    x.font, x.border = fonte, BORDA
    x.alignment = Alignment(vertical="top", wrap_text=quebra, indent=recuo)
    if fmt:
        x.number_format = fmt
    return x


# ------------------------------------------------------------------ dados
def modelo_eap(man: dict) -> list[dict]:
    """39 nos do modelo (iguais nas 4 rodovias; a base e a arvore da RD-101), caminho relativo a 04. Engenharia."""
    nos = []
    for r in man["road_trees"]["RD-101"]:
        rel = nfc(r["relative_parceiro_path"]).split("/04. Engenharia", 1)[1].lstrip("/")
        nos.append({"nivel": r["level"], "nome": nfc(r["label"]), "rel": rel, "desc": nfc(r["description"]),
                    "linha_eap": r["sheet_row"], "celula": r["flux_cell"]})
    for n in man["notes"]:
        alvo = next((x for x in nos if x["nome"] == nfc(n["label"]) and x["nivel"] in (1, 2)), None)
        if alvo:
            alvo["nota"] = NOTA_N.get(n["cell"])
    return nos


def regex_no(rel: str) -> re.Pattern:
    partes = [r"[^/]+" if re.fullmatch(r"\[.*\]", s) else re.escape(s) for s in rel.split("/")]
    return re.compile("^" + "/".join(partes) + "$")


def no_da_eap(rel: str, nos: list[dict], cache: dict) -> str:
    """No mais profundo do modelo que casa com o inicio (segmentos inteiros) do caminho."""
    if rel in cache:
        return cache[rel]
    segs = rel.split("/") if rel else []
    melhor = "(raiz 04. Engenharia)"
    prof = -1
    for n in nos:
        if not n["rel"]:
            continue
        k = n["rel"].count("/") + 1
        if k <= len(segs) and k > prof and n["re"].match("/".join(segs[:k])):
            melhor, prof = n["rel"], k
    cache[rel] = melhor
    return melhor


def carregar(con, c, foto: int, foto_base: int, nos):
    arvores = {go: nfc(p) for go, p in c["destino"]["arvores"].items()}
    nossos = {r[0] for r in con.execute("SELECT fileid_destino FROM plano WHERE fileid_destino IS NOT NULL")}
    base = {r[0] for r in con.execute("SELECT fileid FROM parceiro_item WHERE foto_id=? AND eh_pasta=0", (foto_base,))}
    criadas = {nfc(r[0]) for r in con.execute("SELECT caminho FROM pasta_criada WHERE resultado='CRIADA'")}
    pastas: dict[tuple, dict] = {}
    for cam, eh_pasta, tam, fid in con.execute(
            "SELECT caminho, eh_pasta, tamanho, fileid FROM parceiro_item WHERE foto_id=?", (foto,)):
        cam = nfc(cam)
        go = next((g for g, r in arvores.items() if cam == r or cam.startswith(r + "/")), None)
        if not go:
            continue
        rel = cam[len(arvores[go]) + 1:] if cam != arvores[go] else ""
        if eh_pasta:
            pastas.setdefault((go, rel), {"criada": cam in criadas})
            pastas[(go, rel)]["criada"] = cam in criadas
            continue
        pai = rel.rsplit("/", 1)[0] if "/" in rel else ""
        d = pastas.setdefault((go, pai), {"criada": False})
        d["arq"] = d.get("arq", 0) + 1
        d["bytes"] = d.get("bytes", 0) + (tam or 0)
        orig = "nossos" if fid in nossos else ("pre" if fid in base else "ext")
        d[orig] = d.get(orig, 0) + 1
    cache: dict = {}
    linhas = []
    for (go, rel), d in sorted(pastas.items(), key=lambda x: (RODOVIAS.index(x[0][0]), x[0][1].lower())):
        linhas.append({"go": go, "rel": rel, "nivel": rel.count("/") + 1 if rel else 0,
                       "no": no_da_eap(rel, nos, cache), "arq": d.get("arq", 0), "bytes": d.get("bytes", 0),
                       "nossos": d.get("nossos", 0), "pre": d.get("pre", 0), "ext": d.get("ext", 0),
                       "criada": d["criada"]})
    return linhas


# ------------------------------------------------------------------ planilha EAP
def planilha_eap(destino: Path, c, man, nos, linhas, foto, foto_base, con):
    wb = Workbook()
    # ---- LEIA-ME
    ws = wb.active
    ws.title = "LEIA-ME"
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 110
    ws["A1"] = "EAP G2 — 04. Engenharia — nova arquitetura no PARCEIRO INFRA"
    ws["A1"].font = F_TIT
    f = con.execute("SELECT fim, arquivos, pastas FROM parceiro_foto WHERE id=?", (foto,)).fetchone()
    info = [
        ("Gerado em", datetime.now().strftime("%d/%m/%Y %H:%M")),
        ("Escopo", "PARCEIRO-INFRA/<rodovia>/04. Engenharia das rodovias RD-101, RD-102A, RD-103 e RD-104"),
        ("Fonte da EAP", f"{man['source_file']} (abas {', '.join(man['source_sheets'])}) — sha256 {man['sha256'][:16]}…"),
        ("Fonte da estrutura real", f"Foto {foto} do PARCEIRO (WebDAV, somente leitura), concluida em {f[0]}: "
                                    f"{f[1]:,} arquivos e {f[2]:,} pastas no PARCEIRO-INFRA".replace(",", ".")),
        ("Foto-base (preexistentes)", f"Foto {foto_base}: o que ja estava no PARCEIRO antes das transferencias desta etapa"),
        ("Fora do escopo", "; ".join(nfc(x) for x in man["out_of_scope"])),
        ("", ""),
        ("Abas", ""),
        ("EAP_MODELO", "Os 39 nos da EAP (iguais nas 4 rodovias), a nota e a regra aplicada em cada ramo, o tipo de "
                       "escrita permitido e, por rodovia, quantos arquivos estao hoje no no (formulas)."),
        ("ARQUITETURA_<rodovia>", "A arvore REAL de pastas de cada rodovia ate o 5º nivel abaixo de 04. Engenharia, "
                                  "com arquivos e GiB acumulados (formulas sobre PASTAS_PARCEIRO)."),
        ("RESUMO", "Arquivos e GiB por ramo de primeiro nivel e rodovia, e de quem sao."),
        ("PASTAS_PARCEIRO", "Todas as pastas das 4 arvores na foto final: no da EAP correspondente, arquivos diretos, "
                        "origem dos arquivos e se a pasta foi criada por esta migracao. Base de todas as formulas."),
        ("NOTAS", "As 11 anotacoes da EAP (texto original, links de BM omitidos) e como foram aplicadas."),
        ("REGRAS", "Regras operacionais: corte de BM, ramos bloqueados, ramos so-pasta, escrita condicional."),
        ("", ""),
        ("Origem dos arquivos", "MIGRACAO G2 = enviado por esta operacao; PREEXISTENTE = ja estava na foto-base; "
                                "TERCEIROS DEPOIS = surgiu apos a foto-base e nao foi enviado por esta operacao "
                                "(estagiarios/outros usuarios — sem atribuicao de autoria)."),
        ("Cores", "Azul claro = ramo de 1º nivel; amarelo = ramo so-pasta (RPFO, DUP); salmao = bloqueado (Eventograma)."),
    ]
    for i, (a, b) in enumerate(info, 3):
        ws.cell(i, 1, a).font = F_NEG
        x = ws.cell(i, 2, b)
        x.font, x.alignment = F_TXT, Alignment(wrap_text=True, vertical="top")

    # ---- PASTAS_PARCEIRO (dados; criada antes para as formulas)
    wp = wb.create_sheet("PASTAS_PARCEIRO")
    cab = ["Rodovia", "Caminho (relativo a 04. Engenharia)", "Nível", "Nó da EAP", "Arquivos diretos", "GiB diretos",
           "Enviados pela migração G2", "Preexistentes", "Terceiros depois", "Pasta criada pela migração G2",
           "Ramo (1º nível)"]
    cabecalho(wp, 1, cab, [10, 90, 7, 55, 10, 10, 12, 12, 12, 13, 32])
    for i, r in enumerate(linhas, 2):
        vals = [r["go"], r["rel"] or "(raiz)", r["nivel"], r["no"], r["arq"], r["bytes"] / GIB, r["nossos"],
                r["pre"], r["ext"], "SIM" if r["criada"] else "NÃO", r["rel"].split("/")[0] if r["rel"] else "(raiz)"]
        for j, v in enumerate(vals, 1):
            celula(wp, i, j, v, fmt="0.000" if j == 6 else None)
    n_ult = len(linhas) + 1
    R = lambda col: f"PASTAS_PARCEIRO!${col}$2:${col}${n_ult}"  # noqa: E731

    # ---- EAP_MODELO
    we = wb.create_sheet("EAP_MODELO", 1)
    cab = ["Nível", "Pasta", "Caminho na EAP (relativo a 04. Engenharia)", "Descrição (EAP)", "Nota", "Tipo de escrita",
           "Regra aplicada na migração G2"] + [f"{g} arquivos" for g in RODOVIAS] + ["Total arquivos"]
    cabecalho(we, 1, cab, [6, 40, 55, 40, 6, 14, 70, 11, 11, 11, 11, 12])
    so_pasta = [nfc(x) for x in c["destino"]["so_pasta"]]
    bloq = [nfc(x) for x in c["destino"]["bloqueados"]]
    for i, n in enumerate(nos, 2):
        rel = n["rel"]
        tipo = ("BLOQUEADO" if any(rel == b or rel.startswith(b + "/") for b in bloq) else
                "SÓ PASTA" if any(rel == s or rel.startswith(s + "/") for s in so_pasta) else
                "MODELO (por mês/ID)" if "[" in n["nome"] else "ARQUIVOS")
        vals = [n["nivel"], n["nome"], rel or "(raiz)", n["desc"], n.get("nota"), tipo, regra_de(rel)]
        for j, v in enumerate(vals, 1):
            x = celula(we, i, j, v, fonte=F_NEG if n["nivel"] <= 1 else F_TXT, quebra=j in (4, 7),
                       recuo=n["nivel"] if j == 2 else 0)
            if tipo == "BLOQUEADO":
                x.fill = P_BLOQ
            elif tipo == "SÓ PASTA":
                x.fill = P_SOPASTA
            elif n["nivel"] == 1:
                x.fill = P_N1
        chave = rel or "(raiz 04. Engenharia)"
        for k, go in enumerate(RODOVIAS):
            col = 8 + k
            if rel:   # o no e todos os nos abaixo dele
                fml = (f'=SUMIFS({R("E")},{R("A")},"{go}",{R("D")},$C{i})'
                       f'+SUMIFS({R("E")},{R("A")},"{go}",{R("D")},$C{i}&"/*")')
            else:
                fml = f'=SUMIFS({R("E")},{R("A")},"{go}")'
            celula(we, i, col, fml, fmt="#,##0")
        celula(we, i, 12, f"=SUM(H{i}:K{i})", fonte=F_NEG, fmt="#,##0")
        _ = chave

    # ---- ARQUITETURA por rodovia
    for go in RODOVIAS:
        wa = wb.create_sheet(f"ARQUITETURA_{go}")
        cab = ["Estrutura (até o 5º nível)", "Nível", "Caminho completo (relativo a 04. Engenharia)", "Nó da EAP",
               "Arquivos (acumulado)", "GiB (acumulado)", "Pasta criada pela migração G2"]
        cabecalho(wa, 1, cab, [70, 7, 90, 50, 12, 12, 13])
        lin = 2
        for r in linhas:
            if r["go"] != go or r["nivel"] > 5:
                continue
            nome = r["rel"].rsplit("/", 1)[-1] if r["rel"] else "04. Engenharia"
            n1 = r["nivel"] <= 1
            celula(wa, lin, 1, nome, fonte=F_NEG if n1 else F_TXT, recuo=min(r["nivel"] * 2, 15))
            celula(wa, lin, 2, r["nivel"])
            celula(wa, lin, 3, r["rel"] or "(raiz)")
            celula(wa, lin, 4, r["no"])
            if r["rel"]:
                crit = f'SUBSTITUTE($C{lin},"~","~~")'
                for col, src, fmt in ((5, "E", "#,##0"), (6, "F", "0.000")):
                    celula(wa, lin, col, f'=SUMIFS({R(src)},{R("A")},"{go}",{R("B")},{crit})'
                                         f'+SUMIFS({R(src)},{R("A")},"{go}",{R("B")},{crit}&"/*")', fmt=fmt)
            else:
                celula(wa, lin, 5, f'=SUMIFS({R("E")},{R("A")},"{go}")', fmt="#,##0")
                celula(wa, lin, 6, f'=SUMIFS({R("F")},{R("A")},"{go}")', fmt="0.000")
            celula(wa, lin, 7, "SIM" if r["criada"] else "NÃO")
            if n1:
                for col in range(1, 8):
                    wa.cell(lin, col).fill = P_N1
            lin += 1
        wa.freeze_panes = "B2"

    # ---- RESUMO
    wr = wb.create_sheet("RESUMO", 1)
    wr["A1"] = "Resumo por ramo de 1º nível (foto final do PARCEIRO)"
    wr["A1"].font = F_TIT
    ramos = [n["nome"] for n in nos if n["nivel"] == 1]
    cab = ["Ramo"] + [f"{g} arquivos" for g in RODOVIAS] + ["Total arquivos"] + [f"{g} GiB" for g in RODOVIAS] + ["Total GiB"]
    cabecalho(wr, 3, cab, [36] + [12] * 10)
    for i, ramo in enumerate(ramos + ["(raiz)"], 4):
        celula(wr, i, 1, ramo, fonte=F_NEG)
        for k, go in enumerate(RODOVIAS):
            celula(wr, i, 2 + k, f'=SUMIFS({R("E")},{R("A")},"{go}",{R("K")},$A{i})', fmt="#,##0")
            celula(wr, i, 7 + k, f'=SUMIFS({R("F")},{R("A")},"{go}",{R("K")},$A{i})', fmt="0.00")
        celula(wr, i, 6, f"=SUM(B{i}:E{i})", fonte=F_NEG, fmt="#,##0")
        celula(wr, i, 11, f"=SUM(G{i}:J{i})", fonte=F_NEG, fmt="0.00")
    # pastas de 1o nivel que nao sao da EAP (ex.: '01.Projetos' sem espaco, criada por terceiros na RD-104)
    i = 4 + len(ramos) + 1
    celula(wr, i, 1, "Pastas fora da EAP (nome divergente)", fonte=F_NEG)
    for k, go in enumerate(RODOVIAS):
        for col, src, fmt in ((2 + k, "E", "#,##0"), (7 + k, "F", "0.00")):
            L = get_column_letter(col)
            celula(wr, i, col, f'=SUMIFS({R(src)},{R("A")},"{go}")-SUM({L}4:{L}{i - 1})', fmt=fmt)
    celula(wr, i, 6, f"=SUM(B{i}:E{i})", fonte=F_NEG, fmt="#,##0")
    celula(wr, i, 11, f"=SUM(G{i}:J{i})", fonte=F_NEG, fmt="0.00")
    outros = sorted({r["rel"].split("/")[0] for r in linhas if r["rel"]} - set(ramos))
    wr.cell(i, 12, "; ".join(outros)).font = F_TXT
    t = i + 1
    celula(wr, t, 1, "TOTAL", fonte=F_NEG)
    for col in range(2, 12):
        L = get_column_letter(col)
        celula(wr, t, col, f"=SUM({L}4:{L}{t - 1})", fonte=F_NEG, fmt="#,##0" if col <= 6 else "0.00")
    o = t + 3
    wr.cell(o - 1, 1, "De quem são os arquivos").font = F_NEG
    cabecalho_lin = ["Origem"] + RODOVIAS + ["Total"]
    for j, h in enumerate(cabecalho_lin, 1):
        x = wr.cell(o, j, h)
        x.font, x.fill, x.border = F_CAB, P_CAB, BORDA
    for i, (rot, col) in enumerate((("Enviados pela migração G2", "G"), ("Preexistentes (foto-base)", "H"),
                                    ("Terceiros depois da foto-base", "I")), o + 1):
        celula(wr, i, 1, rot, fonte=F_NEG)
        for k, go in enumerate(RODOVIAS):
            celula(wr, i, 2 + k, f'=SUMIFS({R(col)},{R("A")},"{go}")', fmt="#,##0")
        celula(wr, i, 6, f"=SUM(B{i}:E{i})", fonte=F_NEG, fmt="#,##0")

    # ---- NOTAS
    wn = wb.create_sheet("NOTAS")
    cabecalho(wn, 1, ["Nota", "Célula (Fluxograma)", "Linha EAP", "Pasta", "Texto original da anotação",
                      "Como foi aplicada na migração G2"], [6, 12, 9, 34, 80, 70])
    for i, n in enumerate(man["notes"], 2):
        txt = re.sub(r"https?://\S+", "[link omitido]", nfc(n["text"]))
        no = next((x for x in nos if x["nome"] == nfc(n["label"])), {"rel": nfc(n["label"])})
        vals = [NOTA_N.get(n["cell"]), n["cell"], n["eap_row"], nfc(n["label"]), txt, regra_de(no["rel"])]
        for j, v in enumerate(vals, 1):
            celula(wn, i, j, v, quebra=j in (5, 6))

    # ---- REGRAS
    wg = wb.create_sheet("REGRAS")
    cabecalho(wg, 1, ["Regra", "Valor", "Origem"], [42, 80, 50])
    regras = [(f"BM: último mês de referência — {go}", f"{v[5:]}/{v[:4]}", "Instrução da Emmily, 29/10/2026")
              for go, v in c["destino"]["bm_ate"].items()]
    regras += [
        ("Ramos bloqueados (nenhuma escrita)", "; ".join(bloq), "Nota 11 (Eventograma)"),
        ("Ramos só-pasta (nenhum arquivo)", "; ".join(so_pasta), "Notas 03 e 06"),
        ("Arquivos preexistentes no PARCEIRO", "Nunca apagados, sobrescritos, renomeados ou movidos (exceto BMs fora do "
                                           "período, com autorização expressa de 29/10/2026)", "Prompt G2 / D-12"),
        ("Escrita", "Somente arquivo novo, comprovadamente ausente: PUT If-None-Match:* / MOVE Overwrite:F",
         "Motor G2 (scripts/migra_g2)"),
        ("Duplicidade", "Hash igual (QuickXor) = mesmo documento; não reenviado", "Regra nº 4 do projeto"),
        ("Desapropriação — Fase Executória", "Somente RD-102A, CTD 19, 43 e 45, último envio", "Nota 06"),
        ("Executivo", "Somente revisões finais aprovadas sem ressalva", "Nota 02"),
    ]
    for i, r in enumerate(regras, 2):
        for j, v in enumerate(r, 1):
            celula(wg, i, j, v, quebra=True)

    wb.save(destino)


# ------------------------------------------------------------------ indice
DESCR = [
    (r"^RELATORIO_EXECUTIVO_G2_4_RODOVIAS", "Relatório", "Relatório executivo consolidado das 4 rodovias"),
    (r"^RELATORIO_(RD-[\w]+)\.md$", "Relatório", "Relatório final da rodovia"),
    (r"^RELATORIO_EXECUTIVO_G2\.md$", "Relatório", "Relatório executivo (fase RD-103)"),
    (r"^RELATORIO_TECNICO_G2", "Relatório", "Relatório técnico do motor e da operação"),
    (r"^ESTRUTURA_FINAL_G2", "Relatório", "Estrutura final de pastas da rodovia no PARCEIRO"),
    (r"^CONSOLIDADO_G2\.json", "Relatório", "Números consolidados (máquina) das 4 rodovias"),
    (r"^DECISOES_HUMANAS", "Decisões", "Pendências para decisão humana: PARCEIRO x EAP x divergência x correção sugerida"),
    (r"^CONFLITOS_PARA_DECISAO", "Decisões", "Conflitos de destino ocupado / equivalência incerta"),
    (r"^EXCLUSOES_BM.*SIMULACAO", "Exclusões BM", "Simulação do corte de BM (nada apagado)"),
    (r"^EXCLUSOES_BM", "Exclusões BM", "BMs fora do período excluídos do PARCEIRO (um por linha, com fileid/etag)"),
    (r"^MATRIZ_ORIGEM_DESTINO.*SIMULACAO", "Matriz", "Simulação do planejamento (origem → destino)"),
    (r"^MATRIZ_ORIGEM_DESTINO", "Matriz", "Matriz origem SharePoint → destino PARCEIRO, classificação e motivo"),
    (r"^ARQUIVOS_TRANSFERIDOS", "Matriz", "Arquivos transferidos e validados"),
    (r"^ARQUIVOS_NAO_TRANSFERIDOS", "Matriz", "Arquivos não transferidos e o motivo"),
    (r"^MANIFESTO_HASHES", "Matriz", "Manifesto de hashes (QuickXor/SHA-256) dos enviados"),
    (r"^INVENTARIO_ORIGEM", "Inventário", "Inventário da origem SharePoint G2"),
    (r"^BASELINE_PARCEIRO", "Inventário", "Baseline do PARCEIRO antes das transferências"),
    (r"^JA_EXISTIA_PARCEIRO", "Auditoria", "Arquivos que já existiam no PARCEIRO"),
    (r"^ALTERACOES_PARCEIRO", "Auditoria", "Alterações de terceiros no PARCEIRO durante a migração"),
    (r"^FOTO_PARCEIRO_\d+_(\w+)", "Fotos PARCEIRO", "Fotografia do PARCEIRO (inventário WebDAV)"),
    (r"^TESTE_MOTOR", "Testes", "Teste real do motor de escrita condicional"),
    (r"^01_RELATORIO_GERENCIAL", "Auditoria", "Relatório gerencial da auditoria da rodovia"),
    (r"^02_AUDITORIA_ESTRUTURA_EAP", "Auditoria", "Auditoria da estrutura contra a EAP"),
    (r"^03_AUDITORIA_NOMENCLATURA", "Auditoria", "Auditoria de nomenclatura"),
    (r"^04b_NAO_APROVADOS", "Auditoria", "Resumo dos documentos não aprovados"),
    (r"^04_PENDENTES", "Auditoria", "Pendentes de reavaliação"),
    (r"^05_MATRIZ_DECISOES", "Decisões", "Matriz de decisões humanas da rodovia"),
    (r"^10_PREEXISTENTES_PARCEIRO\.csv", "Auditoria", "Auditoria dos arquivos preexistentes (estagiários/terceiros)"),
    (r"^10_PREEXISTENTES_RESUMO", "Auditoria", "Resumo da auditoria de preexistentes"),
    (r"^EAP_G2_NOVA_ARQUITETURA", "EAP", "Nova arquitetura da EAP 04. Engenharia no PARCEIRO (planilha)"),
    (r"^INDICE_ENTREGAVEIS", "Índice", "Este índice"),
    (r"^G2_EAP_04_ENGENHARIA_REGRAS", "Documentação", "EAP + transcrição das 11 anotações e interpretação operacional"),
    (r"^G2_EAP_04_ENGENHARIA_MANIFESTO", "Documentação", "Manifesto da EAP (estrutura e notas, máquina)"),
    (r"^EAP_Repositorio_G2", "Documentação", "EAP original com notas (Excel)"),
    (r"^OPERACAO_G2", "Documentação", "Manual de operação G2 (comandos, governador)"),
    (r"^STATE_G2", "Documentação", "Estado da operação G2"),
    (r"^HANDOFF", "Documentação", "Handoff para continuidade"),
    (r"^PREPARACAO_CLOUD_G2|^PROMPT_CLAUDE", "Documentação", "Preparação e prompt da migração G2"),
    (r"^SHA256SUMS", "Backup", "Checksums do snapshot"),
]


def descrever(nome: str) -> tuple[str, str]:
    for pad, cat, d in DESCR:
        if re.search(pad, nome):
            return cat, d
    return "Outros", ""


def planilha_indice(destino: Path, c):
    out = saida(c)
    arquivos = [p for p in sorted(out.rglob("*")) if p.is_file() and not p.name.startswith("~$")]
    docs = sorted(p for p in (RAIZ / "docs/migracao_parceiro/g2").glob("*") if p.is_file())
    docs += [p for p in (RAIZ / "docs/migracao_parceiro/g2/privado").glob("*") if p.is_file()]
    docs += [p for p in (RAIZ / "docs").glob("HANDOFF*G2*") if p.is_file()]
    bk = RAIZ / "backup/migracao_parceiro_g2"
    backups = sorted(p for p in bk.iterdir() if p.is_dir()) if bk.exists() else []
    if destino not in arquivos:
        arquivos.append(destino)
    wb = Workbook()
    ws = wb.active
    ws.title = "INDICE"
    ws["A1"] = "Índice dos entregáveis — migração G2 → PARCEIRO INFRA (4 rodovias)"
    ws["A1"].font = F_TIT
    ws["A2"] = f"Gerado em {datetime.now():%d/%m/%Y %H:%M}. Clique no caminho para abrir. Nada aqui vai para o Git."
    ws["A2"].font = F_TXT
    cab = ["Categoria", "Arquivo", "Rodovia", "Descrição", "Tamanho (KB)", "Modificado", "Caminho completo"]
    cabecalho(ws, 4, cab, [14, 50, 10, 60, 12, 17, 110])
    lin = 5
    linhas = []
    for p in arquivos + docs:
        cat, d = descrever(p.name)
        if p.parent.name.startswith("privado"):
            cat = "Documentação"
        m = re.search(r"RD-(101|102A|103|104)", str(p.relative_to(RAIZ)))
        linhas.append((cat, p.name, m.group(0) if m else "4 rodovias", d,
                       p.stat().st_size / 1024 if p.exists() else None,
                       datetime.fromtimestamp(p.stat().st_mtime) if p.exists() else None, p))
    for b in backups:
        linhas.append(("Backup", b.name, (re.search(r"RD-\w+", b.name) or [""])[0] or "4 rodovias",
                       "Snapshot de banco e relatórios (pasta, com SHA256SUMS)",
                       sum(f.stat().st_size for f in b.rglob("*") if f.is_file()) / 1024,
                       datetime.fromtimestamp(b.stat().st_mtime), b))
    ordem = ["Índice", "Relatório", "EAP", "Decisões", "Exclusões BM", "Auditoria", "Matriz", "Inventário",
             "Fotos PARCEIRO", "Testes", "Documentação", "Backup", "Outros"]
    linhas.sort(key=lambda x: (ordem.index(x[0]) if x[0] in ordem else 99, x[2], x[1]))
    for cat, nome, go, d, kb, mod, p in linhas:
        for j, v in enumerate([cat, nome, go, d, kb, mod], 1):
            celula(ws, lin, j, v, fmt="#,##0" if j == 5 else ("dd/mm/yyyy hh:mm" if j == 6 else None))
        x = celula(ws, lin, 7, str(p), fonte=F_LINK)
        x.hyperlink = p.as_uri()
        lin += 1
    # resumo por categoria (formulas)
    wr = wb.create_sheet("RESUMO")
    cabecalho(wr, 1, ["Categoria", "Arquivos", "Tamanho (MB)"], [18, 10, 14])
    for i, cat in enumerate(ordem, 2):
        celula(wr, i, 1, cat, fonte=F_NEG)
        celula(wr, i, 2, f'=COUNTIF(INDICE!$A$5:$A${lin - 1},$A{i})', fmt="#,##0")
        celula(wr, i, 3, f'=SUMIF(INDICE!$A$5:$A${lin - 1},$A{i},INDICE!$E$5:$E${lin - 1})/1024', fmt="#,##0.0")
    t = len(ordem) + 2
    celula(wr, t, 1, "TOTAL", fonte=F_NEG)
    celula(wr, t, 2, f"=SUM(B2:B{t - 1})", fonte=F_NEG, fmt="#,##0")
    celula(wr, t, 3, f"=SUM(C2:C{t - 1})", fonte=F_NEG, fmt="#,##0.0")
    wb.save(destino)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--foto", type=int)
    ap.add_argument("--foto-base", type=int, default=13)
    ap.add_argument("--so-indice", action="store_true")
    a = ap.parse_args()
    c = cfg()
    out = saida(c)
    if not a.so_indice:
        con = conectar()
        foto = a.foto or con.execute("SELECT MAX(id) FROM parceiro_foto WHERE fim IS NOT NULL").fetchone()[0]
        man = json.loads((RAIZ / c["eap"]["manifesto"]).read_text(encoding="utf-8"))
        nos = modelo_eap(man)
        for n in nos:
            n["re"] = regex_no(n["rel"]) if n["rel"] else None
        linhas = carregar(con, c, foto, a.foto_base, nos)
        p = out / "EAP_G2_NOVA_ARQUITETURA.xlsx"
        planilha_eap(p, c, man, nos, linhas, foto, a.foto_base, con)
        print(f"EAP: foto {foto}, {len(linhas)} pastas -> {p}")
    p = out / "INDICE_ENTREGAVEIS_G2.xlsx"
    planilha_indice(p, c)
    print(f"indice -> {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

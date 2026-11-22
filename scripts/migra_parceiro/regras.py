"""
regras - classificacao origem -> destino (EAP) da pasta "5. Compartilhado PARCEIRO".

Fonte das regras: demanda da Emmily de 19/10/2026 (secao 8) + EAP oficial.

Principios:
- So a PASTA-PACOTE de topo e renomeada para o padrao da EAP; o miolo de
  cada pacote (respostas, prestacoes, produtos) segue com a arvore e os nomes
  originais. Pacote entregue a orgao nao e reorganizado por dentro.
- Nome de arquivo nao muda, exceto normalizacao tecnica NFC (mesmo nome
  visivel, bytes de unicode diferentes).
- Data, referencia, entidade (PARCEIRO x Estruturadora) e numero de trimestre
  nunca sao inventados: viram DECISAO pendente, nomeada, que bloqueia so os
  arquivos afetados.

Cada regra devolve Classe(escopo, regra, destino_eap, decisoes, observacao).
`destino_eap` usa os nomes da EAP; o casamento com a pasta real do PARCEIRO
(ex.: "01.Coordenação Programa" sem espaco) e feito depois, por pasta_destino.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

COORD = "01. Coordenação Programa"
PROG = "02. Programa"
PC = f"{PROG}/03. Prestação de Contas"
ORGAOS = f"{PROG}/04. Atendimento aos Órgãos de Controle"

GRUPOS = {
    "G1": "G1 - Coordenação Geral Executiva",
    "G2": "G2 - Gestão de Engenharia e Socioambiental",
    "G3": "G3 - Certificação de Projetos",
    "G4": "G4 - Gestão de Projetos e Contabilidade",
    "G5": "G5 - Assessoramento e Consultoria Jurídica",
}
MESES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
         "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
_MES_IDX = {unicodedata.normalize("NFKD", m).encode("ascii", "ignore").decode().lower(): i + 1
            for i, m in enumerate(MESES)}

# Decisoes abertas: codigo -> pergunta. O plano conta arquivos/bytes bloqueados por cada uma.
DECISOES = {
    "REF_MP_14": "14_Resposta MP: referencia do MP (oficio/procedimento) - nao consta nos documentos do pacote",
    "REF_MP_16": "16_Resposta MP 19 06 26: referencia do MP (oficio/procedimento) - nao consta nos documentos do pacote",
    "REQUISICAO": "0. Geral/REQUISICAO Nº 22026: NAO MIGRAR - pendente de reavaliacao (decisao da Emmily)",
    "CORRESPONDENCIA": "05. Produtos/00. Correspondências (Carta_05.2026_PROG): vai para 02. Programa/01. Correspondências?",
    "SEM_REGRA": "arquivo fora de qualquer regra: classificar manualmente",
}

# Decisoes ja resolvidas: codigo -> (resolucao, fonte). Vao para o relatorio de evidencias.
RESOLVIDAS = {
    "OBRA_RD102": ("RD-102 = RD-102A", "Emmily, 19/10/2026 (secao 10.1)"),
    "ENTIDADE_PC": ("prestacao de contas Programa = 03. Estruturadora",
                    "Programa se apresenta como 'Consórcio Estruturador do Programa'; Órgão Contratante: "
                    "'Prestacao de Contas Mensal do Consorcio Estruturador - Programa' (DOC-16, abr/26) e "
                    "Despacho 000/0000/Órgão Contratante/DCI 'Prestacao de Contas out-dez/2025 - Consorcio Estruturador Programa'; "
                    "hipotese da Emmily (10.5) confirmada"),
    "ANO_PC_MENSAL": ("ABRIL, MAIO, JUNHO = 2026",
                      "DOC-16 'competencia abril/2026'; GEAPF-25 'Maio-2026'; 'Competencia Junho026'"),
    "TRIMESTRE_SET_NOV": ("'Set a Nov 25' = Trimestre 1 de cada obra (RD-104: out-dez/25)",
                          "Relatorio Financeiro '1T Set a Nov.25' (RD-102A); '2T Dez a Fev.26' (RD-101); "
                          "'1o TRIMESTRE/RD104/2025 - Prestacao Trimestral (Out a Dez)'; indice 09/RD104 lista out-dez/25"),
    "REF_TCE_17": ("2026-10-06 - Resposta TCE - RD-101-Requisição 13-2026",
                   "Manifestacao no 031/2026 (06/10/2026): Requisicao no 13/2026-SERV-FISCINFRAENG (TCE-Estado), "
                   "Contrato 08/2025-PARCEIRO, RD-101"),
    "REF_TCE_RD104": ("2026-08-13 - Resposta TCE - RD-104-Requisição 10-2026",
                      "PA-204.1 (13/08/2026): Requisicao no 10/2026-SERV-FISCINFRAENG (TCE-Estado), Contrato 09/2025-PARCEIRO"),
    "DATA_MP_14": ("2026-06-03", "copia de trabalho em '2. Compartilhado interno/11_MP-Estado/14_Resposta MP 03 06 26' "
                                 "(nome da pasta; o pacote da pasta 5 diz so '06 26')"),
}

NOME_TCE_17 = RESOLVIDAS["REF_TCE_17"][0]
NOME_TCE_RD104 = RESOLVIDAS["REF_TCE_RD104"][0]
PC_ESTR = f"{PC}/03. Estruturadora"


@dataclass
class Classe:
    escopo: str                          # migrar | fora | pendente
    regra: str
    destino_eap: str | None = None       # pasta final (nomes da EAP), sem o nome do arquivo
    decisoes: list[str] = field(default_factory=list)
    observacao: str = ""


def mes_de(texto: str) -> tuple[int, int] | None:
    """'01 - Julho - 2025', '04. Outubro 2025', '15. Setembro 2026' -> (2025, 7)."""
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    ano = re.search(r"(20\d\d)", t)
    for nome, idx in _MES_IDX.items():
        if re.search(rf"\b{nome}\b", t):
            return (int(ano.group(1)) if ano else None, idx)
    return None


def pasta_mes(ano: int, mes: int) -> str:
    return f"{ano:04d}-{mes:02d} - {MESES[mes - 1]}"


def obra(texto: str) -> str | None:
    """Normaliza o codigo da obra. RD-102 = RD-102A (decisao da Emmily, 19/10/2026)."""
    m = re.fullmatch(r"RD[\s_-]?(\d{3})(A?)", texto.strip().upper())
    if not m:
        return None
    num, suf = m.groups()
    if num == "102":
        suf = "A"
    return f"RD-{num}{suf}"


def obra_no_nome(nome: str) -> str | None:
    m = re.search(r"RD[\s_-]?(\d{3})A?", nome.upper())
    return obra(m.group(0)) if m else None


def trimestre_ano(texto: str) -> str | None:
    """'jan a mar-26' -> 2026-T1; 'ABR A JUN - 2026' -> 2026-T2; 'Jan a Mar - 26' -> 2026-T1."""
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    ano = re.search(r"(?:20)?(\d{2})\s*$", t.strip())
    inicio = {"jan": 1, "abr": 2, "jul": 3, "out": 4}
    for mes, tri in inicio.items():
        if re.match(rf"\s*{mes}\w*\s*a\s*\w+", t) and ano:
            return f"20{ano.group(1)}-T{tri}"
    return None


def resto(partes: list[str], a_partir: int) -> str:
    """Subcaminho preservado (sem o nome do arquivo)."""
    return "/".join(partes[a_partir:-1])


def juntar(*segs: str) -> str:
    return "/".join(s for s in segs if s)


def classificar(caminho_rel: str) -> Classe:
    p = caminho_rel.split("/")
    n1 = p[0]
    n2 = p[1] if len(p) > 2 else ""

    if n1 == "06. CERTIFICADORA ENTREGA":
        return Classe("fora", "R00_certificadora_fora_do_escopo",
                      observacao="excluido do escopo por decisao da Emmily (19/10/2026); nao apagar")

    if n1 == "0. Geral":
        if n2.startswith("Análise Prestação de Contas out a dez 2025"):
            return Classe("migrar", "R01_geral_analise_pc_2025T4",
                          juntar(PC_ESTR, "02. Trimestral", "2025-T4", resto(p, 2)),
                          observacao="Despacho 000/0000/Órgão Contratante/DCI: prestacao out-dez/2025 do Consorcio Estruturador Programa")
        if n2 == "Documentos Consorciadas":
            periodo = p[2] if len(p) > 3 else ""
            tri = trimestre_ano(periodo)
            miolo = juntar(tri or periodo, resto(p, 3)) if periodo else resto(p, 2)
            return Classe("migrar", "R02_geral_docs_consorciadas",
                          juntar(COORD, "01. Gestão do Contrato", "01. Documentos das Consorciadas", miolo),
                          observacao=(f"periodo '{periodo}' -> '{tri}' (padrao [AAAA-Tn] da EAP); " if tri else "")
                          + "certidoes por empresa preservadas")
        if n2.upper().startswith("REQUISICAO"):
            return Classe("pendente", "R03_geral_requisicao_nao_migrar", None, ["REQUISICAO"],
                          "NAO MIGRAR - pendente de reavaliacao; nao apagar")

    if n1 == "04. RD104" and "resposta tce" in n2.lower():
        return Classe("migrar", "R04_rd104_resposta_tce",
                      juntar(ORGAOS, "02. TCE-Estado", NOME_TCE_RD104, resto(p, 2)),
                      observacao=RESOLVIDAS["REF_TCE_RD104"][1])

    if n1 == "05. Produtos":
        if n2 == "ARTs":
            return Classe("migrar", "R06_produtos_arts",
                          juntar(COORD, "01. Gestão do Contrato", "02. ARTs", resto(p, 2)))
        if n2.startswith("00. Correspond"):
            return Classe("pendente", "R07_produtos_correspondencias",
                          juntar(PROG, "01. Correspondências", resto(p, 2)), ["CORRESPONDENCIA"])
        am = mes_de(n2)
        if am and am[0]:
            g = p[2] if len(p) > 3 else ""
            if g.upper() in GRUPOS:
                return Classe("migrar", "R05_produtos_mes_grupo",
                              juntar(COORD, "02. Produtos Mensais", pasta_mes(*am), GRUPOS[g.upper()],
                                     resto(p, 3)))
            if len(p) == 3:   # arquivo solto na raiz do mes (ex.: indice de entrega)
                return Classe("migrar", "R05c_produtos_raiz_do_mes",
                              juntar(COORD, "02. Produtos Mensais", pasta_mes(*am)),
                              observacao="arquivo da raiz do mes fica na raiz do mes")
            return Classe("pendente", "R05b_produtos_sem_grupo",
                          juntar(COORD, "02. Produtos Mensais", pasta_mes(*am), resto(p, 2)),
                          ["SEM_REGRA"], "subpasta do mes fora de G1-G5")

    if n1 == "07_Prestação de Contas Trimestral - Programa":
        tri = trimestre_ano(n2)
        if tri:
            return Classe("migrar", "R08_pc_trimestral_prog",
                          juntar(PC_ESTR, "02. Trimestral", tri, resto(p, 2)),
                          observacao=RESOLVIDAS["ENTIDADE_PC"][0])

    if n1 == "10. Prestação de Contas Anual Programa" and re.fullmatch(r"20\d\d", n2):
        return Classe("migrar", "R09_pc_anual", juntar(PC, "01. Anual", n2, resto(p, 2)))

    if n1 == "11. Prestação de Contas Mensal Programa":
        am = mes_de(n2)
        if am:
            return Classe("migrar", "R10_pc_mensal_prog",
                          juntar(PC_ESTR, "01. Mensal", pasta_mes(am[0] or 2026, am[1]), resto(p, 2)),
                          observacao=RESOLVIDAS["ANO_PC_MENSAL"][0])

    if n1 == "5_Prestação de Contas (out a dez-25)":
        return Classe("migrar", "R11_pc_out_dez_2025",
                      juntar(PC_ESTR, "02. Trimestral", "2025-T4", resto(p, 1)),
                      observacao=RESOLVIDAS["ENTIDADE_PC"][0])

    if n1 == "8. Prestação de Contas Obras":
        m = re.match(r"(\d)º TRIMESTRE", n2)
        if m and len(p) > 3:
            cod = obra(p[2])
            if cod:
                return Classe("migrar", "R12_pc_obras_trimestre",
                              juntar(PC, "04. Obras", cod, f"Trimestre {m.group(1)}", resto(p, 3)))
        if m and len(p) == 3 and obra_no_nome(p[2]):   # ex.: 'PRIMITIVA RD-101.zip' na raiz do trimestre
            return Classe("migrar", "R12b_pc_obras_raiz_trimestre",
                          juntar(PC, "04. Obras", obra_no_nome(p[2]), f"Trimestre {m.group(1)}"),
                          observacao="obra lida do nome do arquivo")
        cod = obra(n2)
        if cod and len(p) > 3 and "set a nov" in p[2].lower():
            return Classe("migrar", "R13_pc_obras_set_nov",
                          juntar(PC, "04. Obras", cod, "Trimestre 1", resto(p, 2)),
                          observacao=RESOLVIDAS["TRIMESTRE_SET_NOV"][0])

    if n1 == "09. Prestação de Contas Obras - Set a Nov - 25" and n2:
        cod = obra(n2)
        if cod:
            return Classe("migrar", "R13_pc_obras_set_nov",
                          juntar(PC, "04. Obras", cod, "Trimestre 1", "Set a Nov - 25", resto(p, 2)),
                          observacao=RESOLVIDAS["TRIMESTRE_SET_NOV"][0])

    if n1 == "14_Resposta MP 06 26":
        return Classe("pendente", "R14_resposta_mp_14",
                      juntar(ORGAOS, "01. MP-Estado", "2026-06-03 - Resposta MP - {REF}", resto(p, 1)),
                      ["REF_MP_14"], "data: " + RESOLVIDAS["DATA_MP_14"][1])
    if n1 == "16_Resposta MP 19 06 26":
        return Classe("pendente", "R15_resposta_mp_16",
                      juntar(ORGAOS, "01. MP-Estado", "2026-06-19 - Resposta MP - {REF}", resto(p, 1)),
                      ["REF_MP_16"])
    if n1 == "17_Resposta TCE 06 10 26":
        return Classe("migrar", "R16_resposta_tce_17",
                      juntar(ORGAOS, "02. TCE-Estado", NOME_TCE_17, resto(p, 1)),
                      observacao=RESOLVIDAS["REF_TCE_17"][1])

    return Classe("pendente", "R99_sem_regra", None, ["SEM_REGRA"])


def nome_final(nome: str) -> tuple[str, bool]:
    """Nome no destino: o original, so normalizado para NFC. (nome, mudou?)"""
    nfc = unicodedata.normalize("NFC", nome)
    return nfc, nfc != nome

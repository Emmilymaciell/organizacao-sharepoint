"""
366_g2_planejar - classifica, por regra deterministica, os arquivos de origem de UMA rodovia e grava a
matriz origem -> destino (tabela plano + MATRIZ_ORIGEM_DESTINO_G2_<GO>.csv). SO LEITURA: nada e escrito
no PARCEIRO nem no SharePoint. As quatro rodovias usam as MESMAS regras (29/10/2026: RD-101, RD-102A e RD-104
autorizadas pela Emmily); os nomes de pasta da origem, que variam por rodovia (ex.: "4. Relatório Semanal de
Obras" na RD-104, "Hidrologia" na RD-101), sao resolvidos por padrao e precisam ser UNICOS - senao o ramo
fica SEM_EVIDENCIA, nunca adivinhado. Exclusivo da RD-102A (nota 06): CTD-19/43/45 do ultimo envio na pasta
de pleitos; Volumes Finais sem registro de aprovacao ficam pendentes (nota 02: so o aprovado).

Evidencias usadas (tmp/migracao_parceiro_g2/controles/, ver controles.json):
  - 00_ Lista mestre.xlsx           (Controle Geral: codigo, empreendimento, status "Entregue", data)
  - 03 - Acompanhamento de Projetos Executivo.xlsx (Projetos: status de aprovacao por disciplina)
  - 02_Acompanhamento de NC automatizada.xlsm      (VISAO_GERAL: NC, rodovia, abertura, resumo)

Regras por ramo (notas 01-11 da EAP; docs/migracao_parceiro/g2/):
  BM            fonte especial BM_GOxxx; executora -> 01, elaborado Programa/estruturadora -> 03 (nota 04)
  Anteprojeto   tudo de 0_Anteprojeto, espelhando a estrutura da origem (nota 01)
  Executivo     so disciplina com status exatamente "Aprovado" no controle, NT de aprovacao "Entregue" na
                Lista Mestre e pasta REV cuja data coincide (D-3..D+1) com o recebimento da revisao aprovada
                (nota 02). Ressalva, reprovado, em analise, entrega informal: nao copia.
  RPFO, DUP     so pasta (notas 03, 06). Fase Executoria: so RD-102A (nota 06).
  Diario        diarios das subpastas do Relatorio Semanal, ate o fim da ultima semana entregue (nota 07)
  Rel. semanal  codigo RO entregue na Lista Mestre; arquivos da raiz da pasta do relatorio (nota 08)
  Rel. mensal   codigo RM entregue; arquivo principal + Anexos (nota 08)
  NC            codigo NC/NI da planilha de NC, rodovia certa; arquivo de varias NCs -> revisao (nota 09)
  SST           RI (inspecao de alojamento) entregue -> 01; PA/RT de SST entregue -> 02 (nota 10)
  Licenciamento licenca/outorga (atos oficiais) -> 02; RT de condicionante entregue -> 03; PA/NT de
                condicionante entregue -> 04 (nota 05)
  Eventograma   nada (nota 11)
Sempre fora: obsoleto, entrega informal, minuta, Arquivos Auxiliares, .zip, temporarios.
PDF e editavel do mesmo documento na mesma pasta: vai o PDF; o editavel so quando e o unico arquivo.

Uso: python scripts/366_g2_planejar.py --rodovia RD-101 [--simular] [--controles DIR] [--inventario EXEC_ID]
  --simular   nao grava no banco; CSV vai para MATRIZ_..._SIMULACAO.csv (regressao, conferencia)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

import openpyxl  # noqa: E402

from migra_g2.base import (abrir_execucao, agora, cfg, conectar, escrever_csv, fechar_execucao,  # noqa: E402
                           gib, num, pasta, saida)

MESES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro",
         "Outubro", "Novembro", "Dezembro"]
MES_ABREV = {"JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6, "JUL": 7, "AGO": 8, "SET": 9,
             "OUT": 10, "NOV": 11, "DEZ": 12}
EDITAVEIS = {".doc", ".docx", ".xls", ".xlsx", ".xlsm", ".dwg", ".odt"}
LIXO = re.compile(r"^(~\$|\.~lock|thumbs\.db$|desktop\.ini$)|\.(tmp|lnk)$", re.I)
CODIGO = re.compile(r"(?<![A-Z])(RO|RM|RI|RA|RT|PA|NT|NC|NI|AT|BM)\s*[_–\-]?\s*0*(\d{1,4})(?:[.\-](\d{1,2}))?",
                    re.I)

CAB = ["rodovia", "categoria", "classificacao", "motivo", "regra", "evidencia", "revisao", "aprovacao",
       "origem_fonte", "origem_caminho", "item_id", "tamanho", "quick_xor_hash", "modificado_em",
       "destino", "correspondente_parceiro", "estado"]


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def sem_acento(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn").lower()


def mes_pasta(a: int, m: int) -> str:
    return f"{a:04d}-{m:02d} - {MESES[m - 1]}"


def limpo(s: str, n: int = 90) -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", str(s or "")).strip().rstrip(".")
    return re.sub(r"\s+", " ", s)[:n].strip()


def codigos(nome: str) -> list[tuple[str, int, str | None]]:
    return [(p.upper(), int(n), v) for p, n, v in CODIGO.findall(nome)]


def data_br(s: str) -> date | None:
    # "REV02 06 07 26": sem tirar o "REV02", a data lida seria 02/06/2007 (achado de 29/10/2026)
    s = re.sub(r"(?i)^\s*rev\s*\d+\s*[-–]?\s*", "", s)
    m = re.search(r"(\d{1,2})[ ./-](\d{1,2})[ ./-](\d{2,4})", s)
    if not m:
        return None
    d, mm, a = map(int, m.groups())
    a = a + 2000 if a < 100 else a
    try:
        return date(a, mm, d)
    except ValueError:
        return None


# ---------------------------------------------------------------- controles
def aprovado(st) -> bool:
    """'Aprovado', 'Aprovada', 'Aprovado ' (com espaco): sim. 'Aprovado com ressalva(s)': NAO (nota 02)."""
    return sem_acento(str(st or "")).strip() in ("aprovado", "aprovada")


class Controles:
    def __init__(self, rodovia: str, base: Path | None = None):
        base = base or pasta("tmp") / "controles"
        self.meta = {c["nome"]: c for c in json.loads((base / "controles.json").read_text(encoding="utf-8"))}
        go_num = rodovia.split("-")[1]
        # Lista Mestre
        self.lista: dict[tuple[str, int], list[dict]] = defaultdict(list)
        wb = openpyxl.load_workbook(base / "00_ Lista mestre.xlsx", read_only=True, data_only=True)
        for i, r in enumerate(wb["Controle Geral"].iter_rows(min_row=4, values_only=True), start=4):
            if not r or not r[3] or not r[6]:
                continue
            emp = str(r[6]).replace(" ", "")
            if emp.upper() != rodovia.upper() and not (go_num == "102A" and emp.upper() == "RD-102"):
                continue
            for p, n, v in codigos(str(r[3])):
                self.lista[(p, n)].append({"codigo": str(r[3]).strip(), "versao": v, "tipo": r[4], "descricao": r[5],
                                           "data": r[7], "status": r[8], "linha": i, "periodo": r[11],
                                           "relatorio": r[10]})
        # Executivo: aba Projetos
        self.disciplinas = []
        wb = openpyxl.load_workbook(base / "03 - Acompanhamento de Projetos Executivo.xlsx", read_only=True, data_only=True)
        for i, r in enumerate(wb["Projetos"].iter_rows(min_row=2, values_only=True), start=2):
            if r and r[1] and str(r[1]).replace(" ", "").upper() == rodovia.replace(" ", "").upper():
                self.disciplinas.append({"ordem": r[0], "grupo": r[2], "disciplina": (r[3] or "").strip(),
                                         "recebimento": r[6], "ged": r[9], "revisao": r[10], "nt": r[17],
                                         "status": (r[18] or "").strip() if isinstance(r[18], str) else r[18],
                                         "resumo": r[23], "linha": i})
        # NC: aba VISAO_GERAL
        self.ncs: dict[tuple[str, int], dict] = {}
        wb = openpyxl.load_workbook(base / "02_Acompanhamento de NC automatizada.xlsm", read_only=True,
                                    data_only=True, keep_vba=False)
        for i, r in enumerate(wb["VISAO_GERAL"].iter_rows(min_row=4, values_only=True), start=4):
            if not r or not r[0] or not r[1] or str(r[1]).replace(" ", "").upper() != rodovia.upper():
                continue
            doc = str(r[0]).strip()
            cs = codigos(doc)
            if cs and cs[0][0] in ("NC", "NI"):
                self.ncs.setdefault((cs[0][0], cs[0][1]), {"documento": doc, "resumo": r[3], "status": r[4],
                                                            "abertura": r[7], "linha": i})

    def entregue(self, prefixo: str, n: int, versao: str | None = None) -> dict | None:
        """Registro 'Entregue' do codigo. Com versao (NT-175.2 -> '2'), so vale a MESMA versao: uma NT-175.1
        entregue antes nao comprova a aprovacao citada como NT-175.2 (achado A1 da auditoria de 28/10/2026)."""
        for e in self.lista.get((prefixo, n), []):
            if versao is not None and (e["versao"] or "") != str(versao):
                continue
            if str(e["status"]).strip().lower() == "entregue":
                return e
        return None

    def ev(self, e: dict) -> str:
        return f"Lista Mestre linha {e['linha']}: {e['codigo']} {e['status']} em {str(e['data'])[:10]}"


# ---------------------------------------------------------------- planejador
class Plano:
    def __init__(self, rodovia: str, raiz: str):
        self.rodovia, self.raiz = rodovia, raiz
        self.linhas: list[dict] = []

    def add(self, it, categoria, classificacao, motivo, regra="", evidencia="", destino=None, revisao=None,
            aprovacao=None):
        self.linhas.append({"it": it, "categoria": categoria, "classificacao": classificacao, "motivo": motivo,
                            "regra": regra, "evidencia": evidencia, "revisao": revisao, "aprovacao": aprovacao,
                            "destino": nfc(f"{self.raiz}/{destino}") if destino else None})


def preferir_pdf(arquivos: list) -> tuple[list, list]:
    """Na mesma pasta, PDF e editavel com o mesmo nome-base (ou mesmo codigo): fica o PDF."""
    por_chave = defaultdict(list)
    for it in arquivos:
        nome = it["nome"]
        cs = codigos(nome)
        chave = (it["caminho"].rsplit("/", 1)[0], f"{cs[0][0]}{cs[0][1]}.{cs[0][2]}" if cs else Path(nome).stem.lower())
        por_chave[chave].append(it)
    ficam, saem = [], []
    for grupo in por_chave.values():
        pdfs = [i for i in grupo if i["nome"].lower().endswith(".pdf")]
        for i in grupo:
            if pdfs and Path(i["nome"]).suffix.lower() in EDITAVEIS:
                saem.append(i)
            else:
                ficam.append(i)
    return ficam, saem


def outra_rodovia(nome: str, rodovia: str) -> bool:
    """O nome do arquivo cita uma rodovia diferente (RD101, RD-102, RD 104...)?"""
    citadas = {re.sub(r"\D", "", m) for m in re.findall(r"RD[\s_-]?(\d{3})", nome, re.I)}
    return bool(citadas - {re.sub(r"\D", "", rodovia)})


def excluido(caminho: str) -> str | None:
    c = sem_acento(caminho)
    nome = caminho.rsplit("/", 1)[-1]
    if LIXO.search(nome):
        return "arquivo temporario/sistema"
    if "obsolet" in c:
        return "versao marcada como obsoleta na origem"
    if "entrega informal" in c:
        return "entrega informal (nao e entrega formal aprovada)"
    if "minuta" in c:
        return "minuta"
    if "/arquivos auxiliares/" in c or c.startswith("arquivos auxiliares/"):
        return "arquivo auxiliar de elaboracao (nao integra a entrega)"
    if nome.lower().endswith((".zip", ".rar", ".7z")):
        return "pacote compactado (o conteudo entregue esta nas pastas; nao se desempacota)"
    return None


# executora de cada obra, como aparece nas subpastas das entregas finais de BM (nota 04)
EXECUTORA = {"RD-103": r"\bexecutora c\b", "RD-101": r"\bexecutora a\b", "RD-102A": r"\bexecutora b\b", "RD-104": r"\bexecutora d\b"}

# disciplina do controle 03-Acompanhamento -> (grupo na origem, padrao da pasta da disciplina, sem acento).
# Chave: inicio do nome no controle (22 caracteres sem acento bastam para distinguir). Padrao vazio: a pasta
# e o proprio grupo (OAE). A pasta tem de ser UNICA no grupo; senao, SEM_EVIDENCIA.
DISCIPLINAS = [
    ("Relatório de Atividades Preliminares", "1. Fase de Estudos", r"atividades preliminares|^rap$"),
    ("Estudos de Tráfego", "1. Fase de Estudos", r"trafego"),
    ("Estudos Topográficos", "1. Fase de Estudos", r"topografic"),
    ("Estudos Geológicos", "1. Fase de Estudos", r"geologic"),
    ("Diagnóstico Ambiental Prévio (DAP)", "1. Fase de Estudos", r"^dap$|diagnostico ambiental"),
    ("Documentação para Declaração de Utilidade Pública (DUP)", "1. Fase de Estudos", r"^dup$|utilidade publica"),
    ("Estudos Hidrológicos", "1. Fase de Estudos", r"hidrolog"),
    ("Estudos Geotécnicos e de Pavimentação", "1. Fase de Estudos", r"geotecnic"),
    ("Projeto Geométrico e Interseções", "2. Viario Principal", r"geometric"),
    ("Projeto de Terraplenagem", "2. Viario Principal", r"terraplenagem"),
    ("Projeto de Drenagem e Obras de Arte Correntes", "2. Viario Principal", r"drenagem"),
    ("Projeto de Pavimentação", "2. Viario Principal", r"pavimentacao"),
    ("Projeto de Sinalização e Obras Complementares", "3. Viario Secundario", r"sinalizacao"),
    ("Projeto de Desapropriação", "3. Viario Secundario", r"desapropriacao"),
    ("Relatório de Componente Ambiental", "3. Viario Secundario", r"componente ambiental"),
    ("Projeto de Paisagismo", "3. Viario Secundario", r"paisagismo"),
    ("Projeto de Interferências", "3. Viario Secundario", r"interferencia"),
    ("Orçamento e Memoriais de Cálculo", "3. Viario Secundario", r"^orcamento( e cronograma)?$"),
    ("Cronograma e Planos de Execução", "3. Viario Secundario", r"^orcamento"),   # 30/10: dentro de Orcamento
    ("Projeto de Obra de Arte Especial", "4. OAE", ""),
    ("Memoriais de Cálculo Estrutural", "4. OAE", ""),
]
GRUPOS = {"1. Fase de Estudos": "01. FASE DE ESTUDOS", "2. Viario Principal": "02. VIARIO PRINC",
          "3. Viario Secundario": "03. VIARIO SEC", "4. OAE": "04. OAE"}
CTDS_102A = (19, 43, 45)                          # nota 06: so estes, so o ultimo envio de cada
TODAS_APROVADAS = {"RD-102A", "RD-103"}             # 30/10/2026: todas as disciplinas aprovadas -> ultima revisao


def num_rev(nome: str) -> int | None:
    """'REV 05 - 23 07 26', 'REV05', 'REV 02 - ... - APROVADO', 'Rev.3' -> numero da revisao."""
    m = re.search(r"\brev(?:isao)?\.?\s*[-_]?\s*0*(\d{1,3})\b", sem_acento(str(nome)), re.I)
    return int(m.group(1)) if m else None


def planejar(con, ctl: Controles, raiz: str, go: str, inventario: int | None = None) -> Plano:
    P = Plano(go, raiz)
    pref = go.replace("-", "")                     # RD103, RD101, RD102A, RD104 (pastas da origem)
    sql = ("SELECT * FROM origem_item WHERE eh_pasta=0 AND ((fonte='Programa' AND caminho LIKE ?) OR fonte=?)"
           + (" AND execucao_id=?" if inventario else ""))
    itens = [dict(r) for r in con.execute(sql, (f"{pref}/%", f"BM_{pref}") + ((inventario,) if inventario else ()))]
    feitos = set()
    avisos: list[str] = []
    P.avisos = avisos

    def marca(it):
        feitos.add((it["drive_id"], it["item_id"]))

    prog = [i for i in itens if i["fonte"] == "Programa"]

    def sob(p):
        return [i for i in prog if i["caminho"].startswith(p + "/")]

    def filha(base, padrao, ramo):
        """Subpasta UNICA de `base` cujo nome (sem acento) casa com o padrao; None (e aviso) se 0 ou varias."""
        nomes = {i["caminho"][len(base) + 1:].split("/")[0] for i in sob(base) if "/" in i["caminho"][len(base) + 1:]}
        achou = sorted(n for n in nomes if re.search(padrao, sem_acento(n)))
        if len(achou) != 1:
            avisos.append(f"{ramo}: {len(achou)} pastas casam com /{padrao}/ em {base} {achou[:4]}")
            return None
        return f"{base}/{achou[0]}"

    # ---- BM (nota 04)
    for it in [i for i in itens if i["fonte"] == f"BM_{pref}"]:
        marca(it)
        partes = it["caminho"].split("/")
        ex = excluido(it["caminho"])
        if ex:
            P.add(it, "BM", "FORA_DO_ESCOPO", ex, "nota 04")
            continue
        m = re.match(r"(\d+)ª MP - ([A-ZÇ]+)[\s-]*(\d{2})", partes[0], re.I)
        if not m or len(partes) < 3:
            P.add(it, "BM", "SEM_EVIDENCIA", "nao foi possivel identificar mes de referencia/categoria", "nota 04")
            continue
        mes = MES_ABREV.get(sem_acento(m.group(2))[:3].upper())
        ano = 2000 + int(m.group(3))
        sub = sem_acento(partes[1])
        if "elaborado" in sub or "gerado" in sub:
            cat = "03. Elaboração Programa"
        elif re.search(EXECUTORA[go], sub) or "documentos" in sub:
            cat = "01. Documentação Executora"
        else:
            P.add(it, "BM", "SEM_EVIDENCIA", f"subpasta de medicao nao reconhecida: {partes[1]}", "nota 04")
            continue
        if not mes:
            P.add(it, "BM", "SEM_EVIDENCIA", f"mes nao reconhecido em {partes[0]}", "nota 04")
            continue
        limite = ((cfg()["destino"].get("bm_ate") or {}).get(go))
        if limite and f"{ano:04d}-{mes:02d}" > limite:
            P.add(it, "BM", "FORA_DO_ESCOPO", f"BM de {ano:04d}-{mes:02d} posterior ao mes-limite {limite} desta "
                  "rodovia no PARCEIRO (instrucao da Emmily, 29/10/2026)", "nota 04 + corte de BM")
            continue
        destino = f"02. Boletim de Medição/01. Mensal/{mes_pasta(ano, mes)}/{cat}/" + "/".join(partes[2:])
        P.add(it, "BM", "AUSENTE_E_ELEGIVEL", f"entrega final de BM (fonte exclusiva da nota H238)", "nota 04",
              f"pasta de entrega final BM{pref} / {partes[0]} / {partes[1]}", destino)

    # ---- Anteprojeto (nota 01)
    A = f"{pref}/0_Anteprojeto"
    for it in sob(A):
        marca(it)
        ex = excluido(it["caminho"])
        if ex:
            P.add(it, "ANTEPROJETO", "FORA_DO_ESCOPO", ex, "nota 01")
            continue
        P.add(it, "ANTEPROJETO", "AUSENTE_E_ELEGIVEL", "acervo do anteprojeto (nota 01: subir tudo organizado)",
              "nota 01", "estrutura espelhada de 0_Anteprojeto (mesmo padrao ja usado no PARCEIRO na RD-101/102A)",
              "01. Projetos/01. Anteprojeto/" + it["caminho"][len(A) + 1:])

    # ---- Executivo (nota 02)
    E = f"{pref}/4_Projeto Executivo"

    def achar_disc(nome_ctl: str):
        for k, g, padrao in DISCIPLINAS:
            if sem_acento(nome_ctl).startswith(sem_acento(k)[:22]):
                return k, g, padrao
        return None, None, None

    decisao_pasta: dict[str, tuple] = {}         # pasta de disciplina na origem -> (rev_folder|None, motivo, evid, rev, aprov)
    for d in ctl.disciplinas:
        k, g, padrao = achar_disc(d["disciplina"])
        if not k:
            continue
        pasta_disc = f"{E}/{g}" if not padrao else filha(f"{E}/{g}", padrao, f"Executivo/{k}")
        if not pasta_disc:
            continue
        st = str(d["status"] or "").strip()
        rev = d["revisao"]
        ev = (f"03-Acompanhamento aba Projetos linha {d['linha']}: {d['disciplina']} REV {rev}, status '{st}', "
              f"NT {d['nt']}, recebimento {str(d['recebimento'])[:10]}")
        if pasta_disc in decisao_pasta and decisao_pasta[pasta_disc][0]:
            continue                               # memoriais OAE / cronograma compartilham a pasta ja decidida
        if go in TODAS_APROVADAS:
            # 30/10/2026, Emmily: "da 102a e 103 todas disciplinas foram aprovadas entao pega a ultima revisao de
            # todas"; "confere na planilha de projetos as rev na planilha do sharepoint na nuvem" (364 rebaixado)
            pastas_rev = sorted({i["caminho"][len(pasta_disc) + 1:].split("/")[0] for i in sob(pasta_disc)
                                 if "/" in i["caminho"][len(pasta_disc) + 1:]})
            pastas_rev = [pr for pr in pastas_rev if not excluido(pr + "/x")]   # o nome da pasta, nao o arquivo
            # (pacote .zip como unico formato da entrega e aceito abaixo, como na regra geral)
            nums = {pr: num_rev(pr) for pr in pastas_rev}
            com_num = [pr for pr in pastas_rev if nums[pr] is not None]
            alvo = num_rev(f"REV {rev}") if rev is not None else None
            # a coluna de revisao da planilha nao e confiavel como teto (ex.: RD-103 DAP: planilha REV 2, mas a REV 03
            # ja tinha a data de recebimento aprovada): vale a MAIOR revisao da origem; a planilha so e conferida
            if com_num:
                mx = max(nums[pr] for pr in com_num)
                ult = sorted((pr for pr in com_num if nums[pr] == mx), key=lambda p: data_br(p) or date.min)
                escolha = ult[-1]
                como = (f"ultima revisao da origem (REV {mx:02d}); planilha indica REV {rev}"
                        + (" — DIVERGENCIA" if alvo is not None and alvo != mx else ""))
                if alvo is not None and alvo != mx:
                    avisos.append(f"{go} {k}: planilha REV {rev} x maior pasta {escolha}")
            else:
                decisao_pasta[pasta_disc] = (None, f"nenhuma pasta de revisao identificavel; pastas: {pastas_rev}",
                                             ev, rev, st)
                continue
            decisao_pasta[pasta_disc] = (escolha, f"ultima revisao aprovada ({como}); 102A/103: todas as disciplinas "
                                         "aprovadas (instrucao da Emmily, 30/10/2026)", ev, rev, st or "Aprovado")
            continue
        if not aprovado(st):
            decisao_pasta[pasta_disc] = (None, f"disciplina com status '{st or 'sem status'}' (so 'Aprovado' sem ressalva)",
                                         ev, rev, st)
            continue
        nt = codigos(str(d["nt"] or ""))
        ent = ctl.entregue(nt[0][0], nt[0][1], nt[0][2]) if nt else None
        if not ent:
            decisao_pasta[pasta_disc] = (None, f"NT de aprovacao {d['nt']} sem registro 'Entregue' na Lista Mestre",
                                         ev, rev, st)
            continue
        receb = d["recebimento"].date() if isinstance(d["recebimento"], datetime) else None
        pastas_rev = sorted({i["caminho"][len(pasta_disc) + 1:].split("/")[0] for i in sob(pasta_disc)
                             if "/" in i["caminho"][len(pasta_disc) + 1:]})
        cand = []
        for pr in pastas_rev:
            if excluido(pr + "/x"):
                continue
            dt = data_br(pr)
            if receb and dt and receb - timedelta(days=3) <= dt <= receb + timedelta(days=1):
                cand.append(pr)
        if len(cand) == 1:
            decisao_pasta[pasta_disc] = (cand[0], "revisao final aprovada sem ressalva", ev + "; " + ctl.ev(ent), rev, st)
        else:
            decisao_pasta[pasta_disc] = (None, f"nenhuma pasta REV unica com a data da entrega aprovada "
                                         f"({receb}); pastas: {pastas_rev}", ev, rev, st)

    PL = f"{E}/0. Pleitos de Obra"
    for it in sob(E):
        marca(it)
        rel = it["caminho"][len(E) + 1:]
        g = rel.split("/")[0]
        if go == "RD-102A" and it["caminho"].startswith(PL + "/"):
            continue                               # nota 06: CTDs tratados abaixo
        if go == "RD-102A" and re.match(r"^\d+\. volumes finais$", sem_acento(g)):
            P.add(it, "EXECUTIVO", "SEM_EVIDENCIA", "volume final sem registro de aprovacao/entrega na Lista Mestre "
                  "nem no 03-Acompanhamento (nota 02: so o aprovado); destino previsto 05. VOLUMES FINAIS",
                  "nota 02")
            continue
        if g not in GRUPOS:
            P.add(it, "EXECUTIVO", "FORA_DO_ESCOPO", f"pasta '{g}' nao e disciplina de projeto (analises, atas, GED, "
                  "pleitos etc.)", "nota 02")
            continue
        pasta_disc = next((p for p in decisao_pasta if it["caminho"].startswith(p + "/")), None)
        if not pasta_disc:
            P.add(it, "EXECUTIVO", "SEM_EVIDENCIA", "disciplina sem linha no controle 03-Acompanhamento", "nota 02")
            continue
        rev_ok, motivo, ev, rev, st = decisao_pasta[pasta_disc]
        resto = it["caminho"][len(pasta_disc) + 1:]
        ex = excluido(it["caminho"])
        if ex and ex.startswith("pacote compactado") and rev_ok and resto.startswith(rev_ok + "/"):
            irmaos = sob(f"{pasta_disc}/{rev_ok}")
            if all(i["nome"].lower().endswith((".zip", ".rar", ".7z")) for i in irmaos):
                ex = None                          # o pacote e o unico formato da entrega aprovada: vai como esta
                motivo = "revisao final aprovada sem ressalva (entregue so como pacote compactado; copiado sem extrair)"
        if rev_ok and resto.startswith(rev_ok + "/") and not ex:
            disc_dest = pasta_disc[len(E) + 1:].split("/")
            dest = f"01. Projetos/02. Executivo/{GRUPOS[disc_dest[0]]}/" + \
                   ("/".join(disc_dest[1:]) + "/" if len(disc_dest) > 1 else "") + resto
            P.add(it, "EXECUTIVO", "AUSENTE_E_ELEGIVEL", motivo, "nota 02", ev, dest, f"REV {rev}", st)
        elif rev_ok:
            P.add(it, "EXECUTIVO", "NAO_APROVADO", ex or f"nao e a pasta da revisao aprovada ({rev_ok})", "nota 02", ev,
                  revisao=f"REV {rev}", aprovacao=st)
        else:
            cls = "NAO_APROVADO" if st and not aprovado(st) else "SEM_EVIDENCIA"
            P.add(it, "EXECUTIVO", cls, motivo, "nota 02", ev, revisao=f"REV {rev}", aprovacao=st)

    # ---- Desapropriacao, fase executoria: so RD-102A, CTD 19/43/45, ultimo envio (nota 06)
    if go == "RD-102A":
        envios: dict[str, list] = defaultdict(list)
        for it in sob(PL):
            envios[it["caminho"][len(PL) + 1:].split("/")[0]].append(it)
        ultimo: dict[int, tuple] = {}
        for env in envios:
            se = sem_acento(env)
            mctd = re.search(r"desapropriacao\s+ctd[-\s]*(\d+)(?:\s*e\s*(\d+))?", se)
            md = re.match(r"(\d{2})\.(\d{2})\.(\d{2})", env)
            if not mctd or not md:
                continue
            quando = date(2000 + int(md.group(1)), int(md.group(2)), int(md.group(3)))
            for n in {int(x) for x in mctd.groups() if x}:
                if n in CTDS_102A and (n not in ultimo or quando > ultimo[n][0]):
                    ultimo[n] = (quando, env)
        for env, arqs in envios.items():
            donos = sorted(n for n, (_, e) in ultimo.items() if e == env)
            pa = next((c for it in arqs for c in codigos(it["nome"]) if c[0] == "PA"), None)
            ent = ctl.entregue("PA", pa[1], pa[2]) if pa else None
            if donos and not ent:
                for it in arqs:
                    P.add(it, "DESAPROPRIACAO", "SEM_EVIDENCIA", f"ultimo envio de CTD {donos} sem parecer 'Entregue' "
                          "na Lista Mestre", "nota 06")
                continue
            validos = []
            for it in arqs:
                if not donos:
                    P.add(it, "EXECUTIVO", "FORA_DO_ESCOPO", "pleito de obra (fora das disciplinas); CTD so do ultimo "
                          "envio de 19, 43 e 45 (nota 06)", "nota 06")
                    continue
                ex = excluido(it["caminho"])
                if ex and not ex.startswith("pacote compactado"):
                    P.add(it, "DESAPROPRIACAO", "FORA_DO_ESCOPO", ex, "nota 06")
                    continue
                validos.append(it)
            ficam, saem = preferir_pdf(validos)
            for it in saem:
                P.add(it, "DESAPROPRIACAO", "FORA_DO_ESCOPO", "editavel de documento entregue em PDF na mesma pasta",
                      "nota 06", ctl.ev(ent))
            for it in ficam:
                rel = it["caminho"][len(PL) + len(env) + 2:]
                citados = {int(x) for x in re.findall(r"ctd[\s_-]*(\d+)", sem_acento(it["nome"]))} & set(donos)
                alvo = citados if citados else (set(donos) if len(donos) == 1 else set())
                if len(alvo) != 1:
                    P.add(it, "DESAPROPRIACAO", "EQUIVALENCIA_INCERTA", f"documento comum a CTD {donos}: nao "
                          "duplicar entre CTDs sem decisao humana", "nota 06", ctl.ev(ent))
                    continue
                n = alvo.pop()
                P.add(it, "DESAPROPRIACAO", "AUSENTE_E_ELEGIVEL", f"ultimo envio da CTD-{n:02d} a Órgão Contratante"
                      + (" (pacote copiado sem extrair)" if it["nome"].lower().endswith(".zip") else ""), "nota 06",
                      f"{ctl.ev(ent)}; envio {env}",
                      f"04. Desapropriação/02. Fase Executória/CTD-{n:02d}/{rel}")

    # ---- Relatorios semanais e diarios (notas 07, 08)
    G = f"{pref}/3_Gestao Tecnica de Obra"
    S = filha(G, r"^4\. relatorio semanal", "Relatorio semanal")
    RS = filha(S, r"^5\. relatorio semanal", "Relatorio semanal/5") if S else None
    ultimo_fim = None
    semanais = defaultdict(list)
    for it in (sob(RS) if RS else []):
        semanais[it["caminho"][len(RS) + 1:].split("/")[0]].append(it)
    for pasta_rs, arqs in sorted(semanais.items()):
        m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})\s*-\s*(\d{4})\.(\d{2})\.(\d{2})\s*(\d+)º", pasta_rs)
        if not m:
            for it in arqs:
                marca(it)
                P.add(it, "REL_SEMANAL", "SEM_EVIDENCIA", f"pasta sem periodo/numero reconhecivel: {pasta_rs}", "nota 08")
            continue
        d1, d2 = date(*map(int, m.group(1, 2, 3))), date(*map(int, m.group(4, 5, 6)))
        ini, fim = min(d1, d2), max(d1, d2)        # RD-101 tem pastas com o periodo invertido na origem
        n = int(m.group(7))
        ent = ctl.entregue("RO", n)
        nome_pasta = f"{ini:%Y-%m-%d} a {fim:%Y-%m-%d} - {n}º Relatório Semanal"
        validos = []
        for it in arqs:
            marca(it)
            rel = it["caminho"][len(RS) + len(pasta_rs) + 2:]
            ex = excluido(it["caminho"])
            if ex:
                P.add(it, "REL_SEMANAL", "FORA_DO_ESCOPO", ex, "nota 08")
            elif "/" in rel:
                P.add(it, "REL_SEMANAL", "EQUIVALENCIA_INCERTA", "subpasta dentro da pasta do relatorio (versao "
                      "alternativa ou relatorio aninhado): decisao humana", "nota 08")
            elif Path(it["nome"]).suffix.lower() in (".xlsx", ".xls", ".xlsm", ".csv"):
                P.add(it, "REL_SEMANAL", "FORA_DO_ESCOPO", "planilha de elaboracao na raiz da pasta (nao e o "
                      "relatorio entregue)", "nota 08")
            elif outra_rodovia(it["nome"], go):
                P.add(it, "REL_SEMANAL", "EQUIVALENCIA_INCERTA", "o nome cita outra rodovia", "nota 08")
            elif not ent:
                P.add(it, "REL_SEMANAL", "SEM_EVIDENCIA", f"RO-{n:03d} sem registro 'Entregue' na Lista Mestre",
                      "nota 08")
            else:
                validos.append(it)
        if ent:
            ultimo_fim = max(ultimo_fim or fim, fim)
        ficam, saem = preferir_pdf(validos)
        for it in saem:
            P.add(it, "REL_SEMANAL", "FORA_DO_ESCOPO", "editavel de documento entregue em PDF na mesma pasta", "nota 08",
                  ctl.ev(ent))
        for it in ficam:
            rel = it["caminho"][len(RS) + len(pasta_rs) + 2:]
            per = f"; periodo na Lista: {ent['periodo']}" if ent.get("periodo") else ""
            P.add(it, "REL_SEMANAL", "AUSENTE_E_ELEGIVEL", "relatorio semanal entregue", "nota 08",
                  ctl.ev(ent) + per, f"06. Relatório de Obras/01. Semanal/{nome_pasta}/{rel}")

    D = filha(S, r"^4\. diario", "Diario de obras") if S else None
    for it in (sob(D) if D else []):
        marca(it)
        ex = excluido(it["caminho"])
        nome = it["nome"]
        if ex:
            P.add(it, "DIARIO", "FORA_DO_ESCOPO", ex, "nota 07")
            continue
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})\s*a\s*(\d{4})-(\d{2})-(\d{2})", nome)
        mf = re.search(r"–\s*([a-zç]+)\s+(\d{4})", nome, re.I)
        if m:
            ini, fim = date(*map(int, m.group(1, 2, 3))), date(*map(int, m.group(4, 5, 6)))
        elif mf and sem_acento(mf.group(1))[:3].upper() in MES_ABREV:
            mm = MES_ABREV[sem_acento(mf.group(1))[:3].upper()]
            ini = date(int(mf.group(2)), mm, 1)
            fim = (date(ini.year + (mm == 12), mm % 12 + 1, 1) - timedelta(days=1))
        else:
            P.add(it, "DIARIO", "SEM_EVIDENCIA", "periodo do diario nao identificado no nome", "nota 07")
            continue
        if not ultimo_fim or fim > ultimo_fim:
            P.add(it, "DIARIO", "SEM_EVIDENCIA", f"periodo ate {fim} alem da ultima semana entregue ({ultimo_fim})",
                  "nota 07")
            continue
        P.add(it, "DIARIO", "AUSENTE_E_ELEGIVEL", "diario de periodo coberto pelos relatorios semanais entregues",
              "nota 07", f"ultima semana entregue (Lista Mestre) termina em {ultimo_fim}",
              f"05. Diário de Obras/{mes_pasta(ini.year, ini.month)}/{nome}")
    for it in (sob(S) if S else []):
        if (it["drive_id"], it["item_id"]) not in feitos:
            marca(it)
            P.add(it, "REL_SEMANAL", "FORA_DO_ESCOPO", "fotos/fichas de campo/memorial: material de elaboracao, "
                  "nao e o relatorio entregue", "nota 08")

    # ---- Relatorio mensal (nota 08)
    M = filha(G, r"^5\. relatorio mensal", "Relatorio mensal")
    num_go = re.escape(pref[2:])                  # 103, 101, 102A, 104
    mensais = defaultdict(list)
    for it in (sob(M) if M else []):
        mensais[it["caminho"][len(M) + 1:].split("/")[0]].append(it)
    for pasta_rm, arqs in sorted(mensais.items()):
        cs = codigos(pasta_rm)
        ent = ctl.entregue("RM", cs[0][1]) if cs and cs[0][0] == "RM" else None
        mes = None
        for it in arqs:
            mm = re.search(rf"RM-\d+\.\d+\s*-\s*RD\s*{num_go}\s*-\s*(\d{{2}})\s+(\d{{4}})", it["nome"], re.I)
            if mm:
                mes = (int(mm.group(2)), int(mm.group(1)))
        validos = []
        for it in arqs:
            marca(it)
            ex = excluido(it["caminho"])
            if ex:
                P.add(it, "REL_MENSAL", "FORA_DO_ESCOPO", ex, "nota 08")
            elif not ent:
                P.add(it, "REL_MENSAL", "SEM_EVIDENCIA", f"{pasta_rm}: sem registro 'Entregue' na Lista Mestre", "nota 08")
            elif not mes:
                P.add(it, "REL_MENSAL", "SEM_EVIDENCIA", "mes de referencia nao identificado", "nota 08", ctl.ev(ent))
            else:
                validos.append(it)
        ficam, saem = preferir_pdf(validos)
        for it in saem:
            P.add(it, "REL_MENSAL", "FORA_DO_ESCOPO", "editavel de documento entregue em PDF na mesma pasta", "nota 08",
                  ctl.ev(ent))
        for it in ficam:
            rel = it["caminho"][len(M) + len(pasta_rm) + 2:]
            P.add(it, "REL_MENSAL", "AUSENTE_E_ELEGIVEL", "relatorio mensal entregue (com anexos)", "nota 08",
                  ctl.ev(ent), f"06. Relatório de Obras/02. Mensal/{mes_pasta(*mes)}/{rel}")

    # ---- Nao conformidades (nota 09)
    N = filha(G, r"^3\. notifica", "Notificacoes")
    for it in (sob(N) if N else []):
        marca(it)
        ex = excluido(it["caminho"])
        if ex:
            P.add(it, "NC", "FORA_DO_ESCOPO", ex, "nota 09")
            continue
        ncs = sorted({(p, n) for p, n, _ in codigos(it["nome"]) if p in ("NC", "NI")})
        if not ncs:
            P.add(it, "NC", "SEM_EVIDENCIA", "arquivo sem numero de NC no nome", "nota 09")
            continue
        if len(ncs) > 1 or re.search(r"N[CI]-\d+\s*,", it["nome"]):
            P.add(it, "NC", "EQUIVALENCIA_INCERTA", f"documento comum a varias NCs {ncs}: nao misturar entre NCs",
                  "nota 09")
            continue
        nc = ctl.ncs.get(ncs[0])
        if outra_rodovia(it["nome"], go):
            P.add(it, "NC", "EQUIVALENCIA_INCERTA", "o nome cita outra rodovia (possivel arquivo trocado)", "nota 09")
            continue
        if not nc:
            P.add(it, "NC", "SEM_EVIDENCIA", f"{ncs[0][0]}-{ncs[0][1]:03d} nao consta na planilha de NC para a {go}",
                  "nota 09")
            continue
        ab = nc["abertura"]
        if not isinstance(ab, datetime):
            P.add(it, "NC", "SEM_EVIDENCIA", f"{nc['documento']}: data de abertura ausente na planilha", "nota 09")
            continue
        pasta_nc = limpo(f"{nc['documento']} - {ab:%Y-%m-%d} - {nc['resumo']}")
        P.add(it, "NC", "AUSENTE_E_ELEGIVEL", "documento da NC registrada na planilha de acompanhamento", "nota 09",
              f"02_Acompanhamento de NC, VISAO_GERAL linha {nc['linha']}: {nc['documento']} ({nc['status']})",
              f"07. Não Conformidades/{mes_pasta(ab.year, ab.month)}/{pasta_nc}/{it['nome']}")

    # ---- SST e ambiental (notas 05, 10)
    AS = filha(G, r"^6\. acompanhamento ambiental", "Acompanhamento ambiental e SST")
    RI = filha(AS, r"^relatorio de inspecao", "Inspecao de alojamento") if AS else None
    pastas_ri = defaultdict(list)
    for it in (sob(RI) if RI else []):
        pastas_ri[it["caminho"][len(RI) + 1:].split("/")[0]].append(it)
    for pr, arqs in pastas_ri.items():
        cod = next((c for it in arqs for c in codigos(it["nome"]) if c[0] == "RI"), None)
        ent = ctl.entregue("RI", cod[1]) if cod else None
        dt = re.match(r"(\d{4})-(\d{2})", pr)
        for it in arqs:
            marca(it)
            rel = it["caminho"][len(RI) + len(pr) + 2:]
            ex = excluido(it["caminho"])
            if ex:
                P.add(it, "SST", "FORA_DO_ESCOPO", ex, "nota 10")
            elif not ent or not dt:
                P.add(it, "SST", "SEM_EVIDENCIA", "relatorio de inspecao sem registro 'Entregue' na Lista Mestre", "nota 10")
            elif "/" in rel:
                P.add(it, "SST", "SEM_EVIDENCIA", "resposta/documento da executora recebido depois da inspecao "
                      "(nao integra o relatorio entregue)", "nota 10", ctl.ev(ent))
            else:
                P.add(it, "SST", "AUSENTE_E_ELEGIVEL", "relatorio de inspecao de alojamento entregue (com anexos)",
                      "nota 10", ctl.ev(ent),
                      f"08. Segurança do Trabalho/{mes_pasta(int(dt.group(1)), int(dt.group(2)))}/"
                      f"01. Relatório de Inspeção de Alojamento/{it['nome']}")

    def por_codigo_lista(it, categoria, regra):
        """PA/RT/NT entregue: SST -> 08/02; condicionante -> 03/04 (ou 03/03 para RT). Senao, pendente."""
        cs = [c for c in codigos(it["nome"]) if c[0] in ("PA", "RT", "NT", "RA")]
        ent = ctl.entregue(cs[0][0], cs[0][1]) if cs else None
        if not ent:
            return P.add(it, categoria, "SEM_EVIDENCIA", "documento sem registro 'Entregue' na Lista Mestre", regra)
        desc = sem_acento(f"{ent['descricao']} {ent['tipo']}")
        dt = ent["data"] if isinstance(ent["data"], datetime) else None
        if "condicionante" in desc:
            sub = "03. Condicionantes" if cs[0][0] in ("RT", "RA") else "04. Pareceres de Avaliação das Condicionantes"
            return P.add(it, "LICENCIAMENTO", "AUSENTE_E_ELEGIVEL", "documento de condicionante entregue", "nota 05",
                         ctl.ev(ent) + f" ({ent['descricao']})", f"03. Licenciamento Ambiental/{sub}/{it['nome']}")
        if any(k in desc for k in ("sst", "seguranca", "saude")) and dt:
            return P.add(it, "SST", "AUSENTE_E_ELEGIVEL", "documento de SST entregue", "nota 10",
                         ctl.ev(ent) + f" ({ent['descricao']})",
                         f"08. Segurança do Trabalho/{mes_pasta(dt.year, dt.month)}/02. Documentação SST/{it['nome']}")
        return P.add(it, categoria, "SEM_EVIDENCIA", f"entregue, mas sem ramo da EAP correspondente "
                     f"({ent['descricao']}): decisao humana", regra, ctl.ev(ent))

    for it in (sob(AS) if AS else []):
        if (it["drive_id"], it["item_id"]) in feitos:
            continue
        marca(it)
        ex = excluido(it["caminho"])
        if ex:
            P.add(it, "SST", "FORA_DO_ESCOPO", ex, "nota 10")
        elif "/parecer/" in sem_acento(it["caminho"]) or "relatorio de acompanhamento ambiental" in sem_acento(it["caminho"]):
            por_codigo_lista(it, "SST", "nota 10")
        else:
            P.add(it, "SST", "SEM_EVIDENCIA", "ficha/registro de campo sem registro de entrega na Lista Mestre", "nota 10")
    ST = filha(pref, r"^8_seguranca", "Seguranca do trabalho")
    for it in (sob(ST) if ST else []):
        marca(it)
        ex = excluido(it["caminho"])
        if ex:
            P.add(it, "SST", "FORA_DO_ESCOPO", ex, "nota 10")
        elif codigos(it["nome"]):
            por_codigo_lista(it, "SST", "nota 10")
        else:
            P.add(it, "SST", "SEM_EVIDENCIA", "documento da executora sem mes de referencia nem registro de entrega",
                  "nota 10")

    L = filha(pref, r"^6_licenciamento", "Licenciamento")
    for it in (sob(L) if L else []):
        marca(it)
        rel = it["caminho"][len(L) + 1:]
        ex = excluido(it["caminho"])
        if ex:
            P.add(it, "LICENCIAMENTO", "FORA_DO_ESCOPO", ex, "nota 05")
        elif rel.startswith("2_Licenca/") and not codigos(it["nome"]):
            P.add(it, "LICENCIAMENTO", "AUSENTE_E_ELEGIVEL", "ato oficial de licenciamento (licenca/outorga/declaracao)",
                  "nota 05", "documento emitido pelo orgao (oficialmente formalizado)",
                  f"03. Licenciamento Ambiental/02. Licença/{rel[len('2_Licenca/'):]}")
        elif codigos(it["nome"]):
            if Path(it["nome"]).suffix.lower() in EDITAVEIS and any(
                    o["caminho"].rsplit(".", 1)[0] == it["caminho"].rsplit(".", 1)[0] and o["nome"].lower().endswith(".pdf")
                    for o in sob(L)):
                P.add(it, "LICENCIAMENTO", "FORA_DO_ESCOPO", "editavel de documento entregue em PDF na mesma pasta",
                      "nota 05")
            else:
                por_codigo_lista(it, "LICENCIAMENTO", "nota 05")
        else:
            P.add(it, "LICENCIAMENTO", "SEM_EVIDENCIA", "estudo/evidencia sem registro de entrega oficial na Lista Mestre",
                  "nota 05")

    # ---- tudo o mais da rodovia
    for it in prog:
        if (it["drive_id"], it["item_id"]) not in feitos:
            raiz2 = "/".join(it["caminho"].split("/")[:3])
            P.add(it, "OUTROS", "FORA_DO_ESCOPO", f"ramo da origem sem correspondencia nesta operacao ({raiz2})", "EAP")
    return P


def planejar_rd103(con, ctl: Controles, raiz: str) -> Plano:
    """Compatibilidade (371_g2_auditoria): as mesmas regras genericas aplicadas a RD-103."""
    return planejar(con, ctl, raiz, "RD-103")


# ---------------------------------------------------------------- cruzamento com o PARCEIRO
def cruzar(con, P: Plano, c: dict, final: bool = False):
    """Destino ja ocupado, duplicidade interna e conteudo igual ja presente nas quatro arvores (baseline).
    final=True (2a passada): o que continua sem hash no CAMINHO EXATO da EAP com o mesmo tamanho (arquivo acima do
    teto de leitura ou leitura falhou) fica JA_EXISTIA_CORRETO com o motivo explicito (30/10/2026)."""
    foto = con.execute("SELECT MAX(id) FROM parceiro_foto WHERE fim IS NOT NULL").fetchone()[0]
    # o que o proprio motor criou nao e "preexistente": senao a classificacao dependeria da ordem de execucao
    nossos = {r[0] for r in con.execute("SELECT fileid_destino FROM plano WHERE fileid_destino IS NOT NULL")}
    parceiro = {nfc(r["caminho"]): dict(r) for r in con.execute("SELECT * FROM parceiro_item WHERE foto_id=? AND eh_pasta=0", (foto,))
            if r["fileid"] not in nossos}
    por_tam = defaultdict(list)
    for r in parceiro.values():
        por_tam[r["tamanho"]].append(r)
    hashes = {(r["fileid"], r["etag"]): r["quick_xor"] for r in con.execute("SELECT * FROM parceiro_hash")}
    vistos: dict[str, dict] = {}
    for ln in P.linhas:
        if ln["classificacao"] != "AUSENTE_E_ELEGIVEL":
            continue
        it, dest = ln["it"], ln["destino"]
        if dest in parceiro:
            r = parceiro[dest]
            h = hashes.get((r["fileid"], r["etag"]))
            if r["tamanho"] == it["tamanho"] and h is None and (it["tamanho"] or 0) >= 1:
                if final:
                    ln["correspondente"] = dest
                    ln["classificacao"] = "JA_EXISTIA_CORRETO"
                    ln["motivo"] = ("o caminho da EAP ja tem arquivo de MESMO NOME E TAMANHO (conteudo nao lido: acima "
                                    "do teto de leitura ou leitura recusada pelo servidor); nada a copiar, nada sobrescrito")
                    continue
                ln.setdefault("pendente_hash", []).append(r)       # segue elegivel: decide na 2a passada
                continue
            ln["correspondente"] = dest
            if h and it["quick_xor_hash"] and h == it["quick_xor_hash"]:
                # 29/10/2026: alguem (estagiario?) ja subiu o MESMO conteudo no caminho exato da EAP
                ln["classificacao"] = "JA_EXISTIA_CORRETO"
                ln["motivo"] = "o caminho da EAP ja tem conteudo identico (QuickXor); nada a copiar"
            else:
                ln["classificacao"] = "EQUIVALENCIA_INCERTA"
                ln["motivo"] = ("o caminho de destino ja existe no PARCEIRO com conteudo "
                                + ("DIFERENTE" if h or r["tamanho"] != it["tamanho"] else "ainda nao comparado")
                                + " (nunca sobrescrito): decisao humana")
            continue
        if dest in vistos:
            o = vistos[dest]["it"]
            if o["quick_xor_hash"] and o["quick_xor_hash"] == it["quick_xor_hash"]:
                ln["classificacao"], ln["motivo"] = "DUPLICIDADE_CONFIRMADA", "mesmo conteudo e mesmo destino de outro item do plano"
            else:
                ln["classificacao"], ln["motivo"] = "EQUIVALENCIA_INCERTA", "dois arquivos de origem para o mesmo destino"
            continue
        vistos[dest] = ln
        mesmos = [r for r in por_tam.get(it["tamanho"], []) if (it["tamanho"] or 0) >= 1]
        for r in mesmos:
            h = hashes.get((r["fileid"], r["etag"]))
            if h and it["quick_xor_hash"] and h == it["quick_xor_hash"]:
                if r["rodovia"] == P.rodovia:
                    # mesmo conteudo ja na arvore desta rodovia: nao cria segunda copia
                    ln["classificacao"], ln["correspondente"] = "JA_EXISTIA_EM_OUTRO_DESTINO", r["caminho"]
                    ln["motivo"] = "conteudo identico (QuickXor) ja presente na arvore desta rodovia em outro caminho"
                    break
                # outra rodovia: contexto documental diferente (shapefile generico, descritivo RBMC, tabela
                # padrao, anexo "GERAL"). Nao e o documento desta obra: copia e registra a coincidencia.
                ln["correspondente"] = r["caminho"]
                ln["motivo"] += f" | conteudo identico em OUTRA rodovia ({r['rodovia']}), registrado sem impedir"
            elif not h:
                ln.setdefault("pendente_hash", []).append(r)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rodovia", required=True)
    ap.add_argument("--simular", action="store_true", help="nao grava no banco (regressao/conferencia)")
    ap.add_argument("--controles", help="pasta com as planilhas de controle (padrao: tmp/.../controles)")
    ap.add_argument("--inventario", type=int, help="execucao do 363 a usar (padrao: a ultima concluida)")
    ap.add_argument("--max-mib-hash", type=float, default=200,
                    help="nao le do PARCEIRO arquivo maior que isto para comparar conteudo (30/10/2026)")
    ap.add_argument("--refazer-rd103", action="store_true",
                    help="replaneja a RD-103 (30/10/2026: Emmily mandou subir a ultima revisao de todas as disciplinas"
                         " da 102A e 103); itens ja executados nunca sao reclassificados")
    a = ap.parse_args()
    c = cfg()
    from migra_g2.governador import baixar_prioridade_propria, esperar_folga
    print("prioridade:", baixar_prioridade_propria())     # o G2 e secundario (29/10/2026)
    esperar_folga(c.get("recursos"))
    if a.rodovia not in c["destino"]["arvores"]:
        print(f"rodovia desconhecida: {a.rodovia}")
        return 1
    if a.rodovia == "RD-103" and not a.simular and not a.refazer_rd103:
        # RD-103 concluida e auditada (28/10/2026): os pendentes aguardam decisao humana; replanejar
        # reclassificaria o historico. So simulacao (decisao da Emmily, 29/10/2026: nao transformar a auditoria
        # em nova transferencia da RD-103).
        print("RD-103: so --simular. O plano gravado da RD-103 e historico auditado e nao e refeito.")
        return 1
    con = conectar()
    if a.inventario is None:
        r = con.execute("SELECT MAX(id) FROM execucao WHERE script='363_g2_inventario_origem' AND estado='concluida'"
                        ).fetchone()
        a.inventario = r[0]
    ex = abrir_execucao(con, "366_g2_planejar", vars(a)) if not a.simular else None
    ctl = Controles(a.rodovia, Path(a.controles) if a.controles else None)
    raiz = c["destino"]["arvores"][a.rodovia]
    P = planejar(con, ctl, raiz, a.rodovia, a.inventario)
    for av in P.avisos:
        print("AVISO:", av)

    # conteudo de mesmo tamanho no PARCEIRO sem hash conhecido: le do PARCEIRO (streaming) e calcula QuickXor
    cruzar(con, P, c)
    pend = {(r["fileid"], r["etag"]): r for ln in P.linhas for r in ln.get("pendente_hash", [])
            if (r["tamanho"] or 0) <= a.max_mib_hash * 2**20}         # 30/10: teto de leitura por arquivo
    if pend:
        from migra_g2.credencial import parceiro
        from migra_parceiro.quickxor import QuickXorHash
        nc = parceiro(c)
        print(f"lendo {len(pend)} arquivo(s) do PARCEIRO de mesmo tamanho para comparar conteudo "
              f"({gib(sum(r['tamanho'] or 0 for r in pend.values()))} GiB, do menor para o maior) ...", flush=True)
        import time as _t
        tot_b = sum(r["tamanho"] or 0 for r in pend.values())
        lido_b = 0
        for k, ((fid, et), r) in enumerate(sorted(pend.items(), key=lambda x: x[1]["tamanho"] or 0), 1):
            esperar_folga(c.get("recursos"))            # leitura de rede: cede ao projeto prioritario
            t0 = _t.time()
            q = QuickXorHash()
            try:
                for b in nc.ler_stream(r["caminho"]):
                    q.update(b)
            except Exception as e:  # noqa: BLE001 (503, arquivo movido no meio): regra 5, registra e segue
                print(f"  leitura falhou ({type(e).__name__}): {r['caminho'][-90:]}", flush=True)
                continue
            lido_b += r["tamanho"] or 0
            if k % 25 == 0 or _t.time() - t0 > 30:
                print(f"  hash {k}/{len(pend)} ({gib(lido_b)}/{gib(tot_b)} GiB) {_t.time() - t0:.0f} s "
                      f"{r['caminho'][-80:]}", flush=True)
            con.execute("INSERT OR REPLACE INTO parceiro_hash (fileid, etag, caminho, tamanho, quick_xor, lido_em)"
                        " VALUES (?,?,?,?,?,?)", (fid, et, r["caminho"], r["tamanho"], q.base64(), agora()))
            con.commit()                                # checkpoint por arquivo: uma queda nao rele o que ja leu
        for ln in P.linhas:
            ln.pop("pendente_hash", None)
    cruzar(con, P, c, final=True)
    for ln in P.linhas:
        ln.pop("pendente_hash", None)

    quando = agora()
    with con:
        if not a.simular:
            con.execute("DELETE FROM plano WHERE rodovia=? AND estado IN ('PLANEJADO')", (a.rodovia,))
        for ln in (P.linhas if not a.simular else []):
            it = ln["it"]
            atual = con.execute("SELECT estado FROM plano WHERE drive_id=? AND item_id=? AND destino_rel IS ?",
                                (it["drive_id"], it["item_id"], ln["destino"])).fetchone()
            if atual:                                  # item ja em execucao: nao reclassifica
                continue
            con.execute(
                "INSERT INTO plano (drive_id, item_id, rodovia, categoria, regra, evidencia, revisao, aprovacao,"
                " classificacao, motivo, destino_rel, correspondente, estado, etag_origem, atualizado_em)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (it["drive_id"], it["item_id"], a.rodovia, ln["categoria"], ln["regra"], ln["evidencia"],
                 ln["revisao"], ln["aprovacao"], ln["classificacao"], ln["motivo"], ln["destino"],
                 ln.get("correspondente"), "PLANEJADO", it["etag"], quando))

    out = saida(c) / f"MATRIZ_ORIGEM_DESTINO_G2_{a.rodovia}{'_SIMULACAO' if a.simular else ''}.csv"
    escrever_csv(out, CAB, ((a.rodovia, ln["categoria"], ln["classificacao"], ln["motivo"], ln["regra"],
                             ln["evidencia"], ln["revisao"], ln["aprovacao"], ln["it"]["fonte"], ln["it"]["caminho"],
                             ln["it"]["item_id"], ln["it"]["tamanho"], ln["it"]["quick_xor_hash"],
                             ln["it"]["modificado_em"], ln["destino"], ln.get("correspondente"), "PLANEJADO")
                            for ln in P.linhas))
    resumo = defaultdict(lambda: [0, 0])
    for ln in P.linhas:
        k = (ln["categoria"], ln["classificacao"])
        resumo[k][0] += 1
        resumo[k][1] += ln["it"]["tamanho"] or 0
    print(f"{'categoria':14s} {'classificacao':28s} {'arquivos':>8s} {'GiB':>8s}")
    for k in sorted(resumo):
        print(f"{k[0]:14s} {k[1]:28s} {num(resumo[k][0]):>8s} {gib(resumo[k][1]):>8s}")
    el = [ln for ln in P.linhas if ln["classificacao"] == "AUSENTE_E_ELEGIVEL"]
    print(f"ELEGIVEIS: {num(len(el))} arquivos, {gib(sum(ln['it']['tamanho'] or 0 for ln in el))} GiB -> {out}")
    if ex:
        fechar_execucao(con, ex, "concluida", {"linhas": len(P.linhas), "elegiveis": len(el), "avisos": P.avisos,
                                               "inventario": a.inventario,
                                               "controles": {k: v["etag"] for k, v in ctl.meta.items()}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

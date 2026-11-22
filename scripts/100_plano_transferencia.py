"""
100_plano_transferencia - E5-PREP: monta o PLANO de transferencia.

NAO MOVE, NAO COPIA, NAO APAGA NADA. Somente leitura do catalogo local.
Produz a lista do que transferir, para a Emmily conferir antes de executar
qualquer coisa pelo Power Automate.

Tres frentes, conforme definido pela Emmily:

  1) PROJETOS  — so disciplinas APROVADAS / APROVADAS COM RESSALVA no
     Power BI. Move todas as revisoes MENOS a ultima (maior REV; empate
     resolvido pela data). Pastas marcadas OBSOLETO / "desconsiderar"
     sao sempre movidas, mesmo sendo da mesma REV da ultima.

  2) PRODUTOS  — pastas ate JUNHO/2026. Julho em diante NAO move.

  3) COMPARTILHADO INTERNO — pastas 4, 5, 6 e 7:
       04 -> tudo
       05 -> tudo, menos julho
       06 -> so REV 1 de 2025
       07 -> so 1 e 2

Saidas:
    output/PLANO_TRANSFERENCIA.csv      (uma linha por pasta de origem)
    output/PLANO_TRANSFERENCIA.md       (resumo + pontos que exigem decisao)
    output/mapa_disciplina_pasta.csv    (para conferencia do de-para)

Uso:
    python scripts\\100_plano_transferencia.py
"""

from __future__ import annotations

import csv
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_util import caminho_banco, exigir_fase_analise, ler_config  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass

GiB = 1024**3
RE_REV = re.compile(r"^REV\s*-?\s*(\d+)(.*)$", re.I)
RE_DATA = re.compile(r"(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})")
MARCA_DESCARTE = re.compile(
    r"(obsolet|desconsiderar|n[aã]o\s*considerar|ausencia|aus[eê]ncia|cancelad)", re.I)

# rodovia no BI  ->  pasta no SharePoint
ROD = {"RD-102A": "RD102A", "RD-103": "RD103", "RD-101": "RD101", "RD-104": "RD104"}

# disciplina do BI -> termos que identificam a pasta (minusculo, sem acento)
DE_PARA = {
    "Relatório de Atividades Preliminares": ["atividades preliminares",
                                             r"rap", "/rap"],
    "Estudos de Tráfego": ["trafego"],
    "Estudos Topográficos": ["topografic", "topografia", "est.topo"],
    "Estudos Geológicos": ["geologic"],
    "Diagnóstico Ambiental Prévio (DAP)": [r"dap", "ambiental previo", "/dap"],
    "Documentação para Declaração de Utilidade Pública (DUP)": [r"dup",
                                                               "utilidade publica", "/dup"],
    "Estudos Hidrológicos": ["hidrologic"],
    "Estudos Geotécnicos e Sondagens": ["geotecnic", "sondagem", "sondagens"],
    "Projeto Geométrico e Interseções": ["geometric"],
    "Projeto de Terraplenagem": ["terraplen"],
    "Projeto de Drenagem e Obra de Arte Corrente (OAC)": ["drenagem", r"oac", "/oac"],
    "Projeto de Pavimentação": ["pavimenta"],
    "Projeto de Sinalização e de Obras Complementares": ["sinalizac", "sinalizaç",
                                                         "obras complementares"],
    "Projeto de Desapropriação": ["desapropria"],
    "Relatório de Componente Ambiental": ["componente ambiental",
                                          r"rca", "/rca"],
    "Projeto de Paisagismo": ["paisagismo"],
    "Projeto de Interferência de Iluminação e Rede Elétrica":
        ["interferencia", "iluminac", "rede eletrica"],
    "Memoriais Descritivos da Obra": ["memorial descritivo", "memoriais descritivos"],
    "Orçamento e Memoriais de Cálculo": ["orcamento"],
    "Cronograma e Planos de Execução": ["cronograma"],
    "Projeto de Obra de Arte Especial (OAE)": [r"oae",
                                              "obra de arte especial", "/oae"],
    "Memoriais de Cálculo Estrutural e Quantitativo": ["memorial de calculo",
                                                       "calculo estrutural"],
}

MESES = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
         "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}


def sa(t: str) -> str:
    """minusculo sem acento"""
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower())
                   if unicodedata.category(c) != "Mn")


def ptbr(t): return t.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
def num(n, c=0): return ptbr(f"{n:,.{c}f}")
def gib(b): return f"{num((b or 0)/GiB, 2)} GiB"


def data_de(txt: str) -> tuple:
    m = RE_DATA.search(txt or "")
    if not m:
        return (0, 0, 0)
    d, mo, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if a < 100:
        a += 2000
    return (a, mo, d)


def tamanho_pasta(con, caminho: str) -> tuple:
    return con.execute(
        "SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem"
        " WHERE eh_pasta=0 AND deletado=0 AND (caminho=? OR caminho LIKE ?)",
        (caminho, caminho + "/%")).fetchone()


# ----------------------------------------------------------------------

def frente_projetos(con, status: dict) -> tuple[list, list]:
    linhas, avisos = [], []
    pastas = [c for (c,) in con.execute(
        "SELECT caminho FROM item_nuvem WHERE eh_pasta=1 AND deletado=0"
        " AND nivel1='3. Desenvolvimento' ORDER BY caminho")]
    pais: dict = {}
    for c in pastas:
        nome = c.rsplit("/", 1)[-1]
        m = RE_REV.match(nome)
        if m:
            pais.setdefault(c.rsplit("/", 1)[0], []).append(
                (c, nome, int(m.group(1)), m.group(2)))

    for pai, revs in sorted(pais.items()):
        mrod = re.search(r"/G2 -  Engenharia/([^/]+)/", pai)
        rod_sp = mrod.group(1) if mrod else "?"
        rod_bi = next((k for k, v in ROD.items() if v == rod_sp), None)
        pai_sa = sa(pai)

        # de-para por termo, validado pelo numero da ultima REV
        # Casamento do de-para.
        #   "@sigla" -> compara com o NOME da pasta (igualdade ou palavra
        #               isolada). Evita que "rap" case com te-RAP-lenagem
        #               e "rca" com o-RCA-mento.
        #   texto     -> substring em qualquer parte do caminho.
        # Vence sempre o termo mais especifico (mais longo).
        nome_pai = sa(pai.rsplit("/", 1)[-1])
        disc, forca = None, -1
        for d, termos in DE_PARA.items():
            for termo in termos:
                if termo.startswith("@"):
                    sigla = termo[1:]
                    achou = (nome_pai == sigla
                             or re.search(r"(?:^|[^a-z0-9])" + sigla
                                          + r"(?:[^a-z0-9]|$)", nome_pai) is not None)
                    peso = len(sigla)
                else:
                    achou = termo in pai_sa
                    peso = len(termo)
                if achou and peso > forca:
                    disc, forca = d, peso

        revs_ord = sorted(revs, key=lambda x: (x[2], data_de(x[3])))
        ultima = revs_ord[-1]
        rev_max = ultima[2]

        chave = (rod_bi, disc) if rod_bi and disc else None
        st = status.get(chave) if chave else None
        aprovado = bool(st and st[0])
        st_txt = st[1] if st else "SEM CORRESPONDENCIA NO BI"

        # confere o numero da REV contra o BI
        confere = ""
        if st and st[2] not in ("", None):
            try:
                confere = "OK" if int(st[2]) == rev_max else f"DIVERGE (BI={st[2]}, SP={rev_max})"
            except ValueError:
                confere = "?"

        if not disc:
            avisos.append(("SEM DE-PARA", pai, f"{len(revs)} revisoes"))
        elif confere.startswith("DIVERGE"):
            avisos.append(("REV DIVERGE", pai, f"{disc} · {confere}"))

        # --- REGRA (definida pela Emmily em 25/09) --------------------
        # O corte e a ULTIMA REVISAO APROVADA NO POWER BI, nao a ultima que
        # existe no SharePoint. Move-se o que esta ABAIXO dela.
        # Revisoes ACIMA da aprovada sao trabalho em tramitacao: ficam.
        try:
            rev_corte = int(st[2]) if st and str(st[2]).strip() != "" else None
        except (ValueError, TypeError):
            rev_corte = None

        # excecao pedida: RD-102A / Interferencias -> manter as DUAS ultimas
        duas_ultimas = ("RD102A" in pai and "interferenc" in sa(pai))
        ids_manter_extra = set()
        if duas_ultimas:
            ids_manter_extra = {r[0] for r in revs_ord[-2:]}

        # dentro da REV de corte, a que fica e a mais recente nao-obsoleta
        mesma_rev = [r for r in revs_ord if rev_corte is not None and r[2] == rev_corte]
        fica_na_corte = None
        if mesma_rev:
            limpas = [r for r in mesma_rev if not MARCA_DESCARTE.search(r[1])]
            fica_na_corte = (limpas or mesma_rev)[-1][0]

        for caminho, nome, n, resto in revs_ord:
            descartavel = bool(MARCA_DESCARTE.search(nome))

            if not aprovado:
                acao, por = "NAO MOVER", f"disciplina nao aprovada no BI ({st_txt})"
            elif rev_corte is None:
                acao, por = "NAO MOVER", "BI nao informa o numero da revisao aprovada"
            elif caminho in ids_manter_extra:
                acao, por = "MANTER", "RD-102A/Interferencias: manter as duas ultimas"
            elif n < rev_corte:
                acao, por = "MOVER", f"anterior a REV {rev_corte} aprovada no BI"
            elif n == rev_corte:
                if caminho == fica_na_corte:
                    acao, por = "MANTER", f"REV {rev_corte} — a aprovada no BI"
                else:
                    acao, por = "MOVER", (
                        "mesma REV da aprovada, marcada obsoleta/desconsiderar"
                        if descartavel else
                        "mesma REV da aprovada, versao superada pela mais recente")
            else:                                   # n > rev_corte
                if descartavel:
                    acao, por = "MOVER", "posterior a aprovada, mas marcada obsoleta"
                else:
                    acao, por = "MANTER", (
                        f"REV {n} e posterior a aprovada (REV {rev_corte}) — "
                        "trabalho em tramitacao, fica")

            arq, bts = tamanho_pasta(con, caminho)
            # destino: PE<rodovia>/<estrutura preservada a partir do projeto>
            rel = caminho.split("4_Projeto Executivo/", 1)[-1]                 if "4_Projeto Executivo/" in caminho else caminho.lstrip("/")
            linhas.append({
                "frente": "1-PROJETOS", "rodovia": rod_sp, "disciplina": disc or "?",
                "status_bi": st_txt, "rev_bi_x_sp": confere,
                "origem": caminho, "pasta": nome, "rev": n,
                "arquivos": arq, "bytes": bts, "acao": acao, "motivo": por,
                "destino": f"PE{rod_sp.replace('RD','')}/{rel}" if acao == "MOVER" else "",
            })
    return linhas, avisos


def frente_produtos(con) -> list:
    linhas = []
    subs = con.execute(
        "SELECT DISTINCT nivel2 FROM item_nuvem WHERE eh_pasta=0 AND deletado=0"
        " AND nivel1='4. Produtos' AND nivel2 IS NOT NULL ORDER BY nivel2").fetchall()
    for (n2,) in subs:
        base = sa(n2)
        mes = ano = None
        for k, v in MESES.items():
            if k in base:
                mes = v
        ma = re.search(r"(20\d{2})", base)
        ano = int(ma.group(1)) if ma else None
        if ano and mes:
            mover = (ano, mes) <= (2026, 6)
            por = ("ate junho/2026" if mover
                   else "julho/2026 em diante — NAO mover")
        else:
            mover, por = False, "sem mes/ano no nome — INVESTIGAR"
        arq, bts = tamanho_pasta(con, f"/4. Produtos/{n2}")
        linhas.append({
            "frente": "2-PRODUTOS", "rodovia": "", "disciplina": "",
            "status_bi": "", "rev_bi_x_sp": "",
            "origem": f"/4. Produtos/{n2}", "pasta": n2, "rev": "",
            "arquivos": arq, "bytes": bts,
            "acao": "MOVER" if mover else ("NAO MOVER" if ano else "INVESTIGAR"),
            "motivo": por,
            "destino": f"Produtos/{n2}" if mover else "",
        })
    return linhas


def frente_interno(con) -> tuple[list, list]:
    linhas, avisos = [], []
    raizes = con.execute(
        "SELECT DISTINCT nivel2 FROM item_nuvem WHERE eh_pasta=0 AND deletado=0"
        " AND nivel1='2. Compartilhado interno' AND nivel2 IS NOT NULL"
        " ORDER BY nivel2").fetchall()
    alvo = {}
    for (n2,) in raizes:
        m = re.match(r"^0?(\d+)[_\-\s]", n2)
        if m and m.group(1) in ("4", "5", "6", "7"):
            alvo[m.group(1)] = n2
    for k in ("4", "5", "6", "7"):
        if k not in alvo:
            avisos.append(("PASTA NAO ENCONTRADA", f"nivel2 iniciando por {k}", ""))

    for k, n2 in sorted(alvo.items()):
        base = f"/2. Compartilhado interno/{n2}"
        filhos = con.execute("""
            SELECT DISTINCT
              substr(caminho, length(?)+2,
                     instr(substr(caminho, length(?)+2), '/')-1) f
              FROM item_nuvem
             WHERE eh_pasta=0 AND deletado=0 AND caminho LIKE ?
             ORDER BY f""", (base, base, base + "/%")).fetchall()
        filhos = [f for (f,) in filhos if f]

        if not filhos:                       # pasta sem subpastas: move inteira
            arq, bts = tamanho_pasta(con, base)
            linhas.append({"frente": "3-INTERNO", "rodovia": "", "disciplina": "",
                           "status_bi": "", "rev_bi_x_sp": "", "origem": base,
                           "pasta": n2, "rev": "", "arquivos": arq, "bytes": bts,
                           "acao": "MOVER", "motivo": "regra: pasta 4 vai inteira",
                           "destino": ""})
            continue

        for f in filhos:
            cam = f"{base}/{f}"
            fs = sa(f)
            if k == "4":
                acao, por = "MOVER", "pasta 4: tudo"
            elif k == "5":
                if "jul" in fs:
                    acao, por = "NAO MOVER", "pasta 5: julho fica"
                else:
                    acao, por = "MOVER", "pasta 5: tudo menos julho"
            elif k == "6":
                # a pasta 6 tem /2025/<n>. <assunto>; "rev 1 de 2025" foi lido
                # como o item 1 dentro de 2025 — MESMO PADRAO DA PASTA 7.
                if "2025" in fs:
                    acao, por = "DESCER", "pasta 6: entrar em 2025 e pegar o item 1"
                else:
                    acao, por = "NAO MOVER", "pasta 6: so 2025"
            else:
                m = re.match(r"^0?([12])\s*[.º°)\-]", f.strip())
                if m:
                    acao, por = "MOVER", f"pasta 7: item {m.group(1)}"
                else:
                    acao, por = "NAO MOVER", "pasta 7: so 1 e 2"

            if acao == "DESCER":
                netos = con.execute("""
                    SELECT DISTINCT
                      substr(caminho, length(?)+2,
                             instr(substr(caminho, length(?)+2), '/')-1) g
                      FROM item_nuvem
                     WHERE eh_pasta=0 AND deletado=0 AND caminho LIKE ?
                     ORDER BY g""", (cam, cam, cam + "/%")).fetchall()
                for (g,) in [x for x in netos if x[0]]:
                    mg = re.match(r"^0?1\s*[.º°)\-]", g.strip())
                    sub = f"{cam}/{g}"
                    a2, p2 = (("MOVER", "pasta 6: item 1 de 2025 — CONFIRMAR")
                              if mg else ("NAO MOVER", "pasta 6: so o item 1"))
                    if mg:
                        avisos.append(("CONFIRMAR REGRA 6", sub,
                                       "interpretei 'rev 1 de 2025' como este item"))
                    aq, bt = tamanho_pasta(con, sub)
                    linhas.append({"frente": "3-INTERNO", "rodovia": "",
                                   "disciplina": "", "status_bi": "",
                                   "rev_bi_x_sp": "", "origem": sub, "pasta": g,
                                   "rev": "", "arquivos": aq, "bytes": bt,
                                   "acao": a2, "motivo": p2, "destino": ""})
                continue

            arq, bts = tamanho_pasta(con, cam)
            linhas.append({"frente": "3-INTERNO", "rodovia": "", "disciplina": "",
                           "status_bi": "", "rev_bi_x_sp": "", "origem": cam,
                           "pasta": f, "rev": "", "arquivos": arq, "bytes": bts,
                           "acao": acao, "motivo": por, "destino": ""})
    return linhas, avisos


def main() -> int:
    cfg = ler_config()
    exigir_fase_analise(cfg)
    saida = Path(cfg["saida"])
    con = sqlite3.connect(caminho_banco(cfg))
    try:
        status = {}
        with open("config/status_bi.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter=";"):
                s = (r["status_2n"] or "").strip()
                status[(r["rodovia"], r["disciplina"])] = (
                    sa(s).startswith("aprovad"), s or "(vazio)", r["rev_bi"])

        proj, av1 = frente_projetos(con, status)
        prod = frente_produtos(con)
        inte, av2 = frente_interno(con)
        todas = proj + prod + inte
        avisos = av1 + av2

        with (saida / "PLANO_TRANSFERENCIA.csv").open(
                "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, delimiter=";", fieldnames=[
                "frente", "rodovia", "disciplina", "status_bi", "rev_bi_x_sp",
                "origem", "pasta", "rev", "arquivos", "bytes", "acao",
                "motivo", "destino"])
            w.writeheader()
            for l in todas:
                w.writerow(l)

        def tot(f, a):
            sel = [x for x in todas if x["frente"] == f and x["acao"] == a]
            return len(sel), sum(x["arquivos"] for x in sel), sum(x["bytes"] for x in sel)

        L = ["# Plano de transferência — para conferência", "",
             "> **NADA FOI MOVIDO, COPIADO OU APAGADO.** Esta é a lista proposta, "
             "para você conferir **antes** de executar qualquer coisa.", "",
             "## Resumo", "",
             "| Frente | Ação | Pastas | Arquivos | Espaço |",
             "|---|---|---:|---:|---:|"]
        for f in ("1-PROJETOS", "2-PRODUTOS", "3-INTERNO"):
            for a in ("MOVER", "NAO MOVER", "MANTER", "INVESTIGAR"):
                n, q, b = tot(f, a)
                if n:
                    L.append(f"| {f} | **{a}** | {num(n)} | {num(q)} | {gib(b)} |")
        n, q, b = (len([x for x in todas if x["acao"] == "MOVER"]),
                   sum(x["arquivos"] for x in todas if x["acao"] == "MOVER"),
                   sum(x["bytes"] for x in todas if x["acao"] == "MOVER"))
        L += ["", f"**TOTAL A MOVER: {num(n)} pastas · {num(q)} arquivos · "
              f"{gib(b)}**", ""]

        if avisos:
            L += ["## ⚠️ Pontos que exigem a sua decisão", "",
                  "| Tipo | Onde | Detalhe |", "|---|---|---|"]
            for t, onde, det in avisos[:40]:
                L.append(f"| **{t}** | `{onde[-70:]}` | {det} |")
            L.append("")

        L += ["## Conferência REV: Power BI x SharePoint", "",
              "| Situação | Casos |", "|---|---:|"]
        ok = len([x for x in proj if x["rev_bi_x_sp"] == "OK"])
        dv = len([x for x in proj if x["rev_bi_x_sp"].startswith("DIVERGE")])
        L += [f"| Número da revisão bate | {num(ok)} |",
              f"| **Diverge — conferir** | **{num(dv)}** |", ""]
        (saida / "PLANO_TRANSFERENCIA.md").write_text("\n".join(L), encoding="utf-8")

        print("=" * 68)
        print("PLANO DE TRANSFERENCIA (nada foi executado)")
        print("=" * 68)
        for f in ("1-PROJETOS", "2-PRODUTOS", "3-INTERNO"):
            print(f"  {f}")
            for a in ("MOVER", "NAO MOVER", "MANTER", "INVESTIGAR"):
                n_, q_, b_ = tot(f, a)
                if n_:
                    print(f"    {a:<12} {n_:>4} pastas  {q_:>7,} arq  {gib(b_):>12}")
        print()
        print(f"  TOTAL A MOVER: {n} pastas · {num(q)} arquivos · {gib(b)}")
        print(f"  avisos que exigem decisao: {len(avisos)}")
        print()
        print(f"  {saida/'PLANO_TRANSFERENCIA.csv'}")
        print(f"  {saida/'PLANO_TRANSFERENCIA.md'}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())

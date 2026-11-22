"""
310_parceiro_mapa - proposta de estrutura `Coordenação Programa` para o repositorio PARCEIRO.

Somente leitura. Nao chama a API, nao copia, nao move, nao renomeia.

Aplica regras fixas (abaixo, em REGRAS) a cada pasta de `5. Compartilhado PARCEIRO`
e diz para onde ela iria dentro de `Coordenação Programa/`. Nome de ARQUIVO nunca
muda: so a pasta. Pasta vazia recebe "nao levar".

`06. CERTIFICADORA ENTREGA` fica fora (pedido da Emmily, 09/10).

Saidas (output/parceiro/):
    PARCEIRO_MAPA_PASTAS.csv        pasta atual -> pasta proposta, com contagem
    PARCEIRO_ESTRUTURA_PROPOSTA.md  arvore proposta com arquivos e GiB

Uso:
    python scripts\\310_parceiro_mapa.py
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_util import caminho_banco, exigir_fase_analise, ler_config  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass

GiB = 1024**3
BRT = timezone(timedelta(hours=-3))
RAIZ5 = "/5. Compartilhado PARCEIRO"
FORA = "06. CERTIFICADORA ENTREGA"
RAIZ_NOVA = "Coordenação Programa"


def num(n, c=0):
    return f"{n:,.{c}f}".replace(",", "_").replace(".", ",").replace("_", ".")


MESES = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
         "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
         "outubro": 10, "novembro": 11, "dezembro": 12}
NOME_MES = {v: k.capitalize() for k, v in MESES.items() if k != "marco"}

GC = "01. Gestão do Contrato"
PM = "02. Produtos Mensais"
PC = "03. Prestação de Contas"
OC = "04. Órgãos de Controle"
OB = f"{PC}/04. Obras"


def mes_produto(m: re.Match) -> str:
    """'01 - Julho - 2025' / '04. Outubro 2025' -> '2025-07 - Julho'"""
    nome = m.group(1)
    a = re.search(r"([A-Za-zçÇ]+)\D*(\d{4})", nome)
    if not a or a.group(1).lower() not in MESES:
        return f"{PM}/{nome} (DEFINIR)" + (m.group(2) or "")
    mes = MESES[a.group(1).lower()]
    return f"{PM}/{a.group(2)}-{mes:02d} - {NOME_MES[mes]}" + (m.group(2) or "")


def mes_mensal(m: re.Match) -> str:
    """'1. ABRIL' -> '2026-04 - Abril' (prestacao mensal de 2026)"""
    a = re.search(r"([A-Za-zçÇ]+)\s*$", m.group(1))
    mes = MESES.get(a.group(1).lower()) if a else None
    if not mes:
        return f"{PC}/01. Mensal/{m.group(1)} (DEFINIR)" + (m.group(2) or "")
    return f"{PC}/01. Mensal/2026-{mes:02d} - {NOME_MES[mes]}" + (m.group(2) or "")


# (padrao sobre o caminho relativo a pasta 5, destino ou funcao, observacao)
# Ordem importa: a primeira que casar vale.
R = r"(/.*)?$"
REGRAS: list[tuple[str, object, str]] = [
    (rf"^0\. Geral/REQUISICAO(.*?){R}", lambda m: f"{GC}/02. Requisições/REQUISICAO{m.group(1)}" + (m.group(2) or ""), ""),
    (rf"^0\. Geral/Documentos Consorciadas/Jan a Mar - 26{R}", lambda m: f"{GC}/03. Documentos das Consorciadas/2026-T1 (jan-mar)" + (m.group(1) or ""), ""),
    (rf"^0\. Geral/Documentos Consorciadas{R}", lambda m: f"{GC}/03. Documentos das Consorciadas" + (m.group(1) or ""), ""),
    (rf"^0\. Geral/Análise Prestação de Contas out a dez 2025{R}", lambda m: f"{OB}/2025-10 a 2025-12 (out-dez)/00. Análise Programa" + (m.group(1) or ""), ""),
    (r"^0\. Geral$", f"{GC}/00. Geral", "arquivos soltos — avaliar se ficam aqui"),
    (rf"^05\. Produtos/00\. Correspondências{R}", lambda m: f"{GC}/01. Correspondências" + (m.group(1) or ""), ""),
    (rf"^05\. Produtos/ARTs{R}", lambda m: f"{GC}/04. ARTs" + (m.group(1) or ""), ""),
    (rf"^05\. Produtos/([^/]+){R}", mes_produto, ""),
    (r"^05\. Produtos$", PM, ""),
    (rf"^07_Prestação de Contas Trimestral - Programa/jan a mar-26{R}", lambda m: f"{PC}/02. Trimestral/2026-T1 (jan-mar)" + (m.group(1) or ""), ""),
    (rf"^07_Prestação de Contas Trimestral - Programa/ABR A JUN - 2026{R}", lambda m: f"{PC}/02. Trimestral/2026-T2 (abr-jun)" + (m.group(1) or ""), ""),
    (r"^07_Prestação de Contas Trimestral - Programa$", f"{PC}/02. Trimestral", ""),
    (rf"^11\. Prestação de Contas Mensal Programa/([^/]+){R}", mes_mensal, ""),
    (r"^11\. Prestação de Contas Mensal Programa$", f"{PC}/01. Mensal", ""),
    (rf"^10\. Prestação de Contas Anual Programa{R}", lambda m: f"{PC}/03. Anual" + (m.group(1) or ""), ""),
    (rf"^8\. Prestação de Contas Obras/(\d)º TRIMESTRE{R}", lambda m: f"{OB}/Trimestre {m.group(1)}" + (m.group(2) or ""), "período do trimestre a confirmar"),
    (rf"^8\. Prestação de Contas Obras/RD101/2025 - Prestação Trimestral \(Set a Nov\){R}", lambda m: f"{OB}/2025-09 a 2025-11 (set-nov)/RD101 - envio complementar" + (m.group(1) or ""), "mesmo período da pasta 09 — confirmar se é reenvio"),
    (r"^8\. Prestação de Contas Obras(/RD101)?$", OB, ""),
    (rf"^09\. Prestação de Contas Obras - Set a Nov - 25{R}", lambda m: f"{OB}/2025-09 a 2025-11 (set-nov)" + (m.group(1) or ""), ""),
    (rf"^5_Prestação de Contas \(out a dez-25\){R}", lambda m: f"{OB}/2025-10 a 2025-12 (out-dez)" + (m.group(1) or ""), ""),
    (rf"^14_Resposta MP 06 26{R}", lambda m: f"{OC}/01. MP-Estado/2026-06 - Resposta MP" + (m.group(1) or ""), "pacote enviado: estrutura interna mantida"),
    (rf"^16_Resposta MP 19 06 26{R}", lambda m: f"{OC}/01. MP-Estado/2026-06-19 - Resposta MP" + (m.group(1) or ""), "pacote enviado: estrutura interna mantida"),
    (r"^04\. RD104$", f"{OC}/02. TCE-Estado", ""),
    (rf"^17_Resposta TCE 06 10 26{R}", lambda m: f"{OC}/02. TCE-Estado/2026-10-06 - Resposta TCE - RD101" + (m.group(1) or ""), "pacote enviado: estrutura interna mantida"),
    (rf"^04\. RD104/2026\.08\.13 - Nova resposta TCE{R}", lambda m: f"{OC}/02. TCE-Estado/2026-08-13 - Nova resposta TCE - RD104" + (m.group(1) or ""), "pacote enviado: estrutura interna mantida"),
]

RE_REV = re.compile(r"^REV\s*-?\s*0*(\d+)(.*)$", re.I)


RE_GO = re.compile(r"^RD[\s_-]*(\d{3})[\s_-]*(A?)$", re.I)


def limpar(segmento: str, pacote: bool = False) -> str:
    """Espacos sempre; REV nn e GOnnn so fora de pacote enviado (Anexo III, regra 4)."""
    s = re.sub(r"\s{2,}", " ", segmento).strip()
    if pacote:
        return s
    m = RE_REV.match(s)
    if m:
        s = f"REV {int(m.group(1)):02d}{m.group(2)}"
    m = RE_GO.match(s)
    if m:
        s = f"RD{m.group(1)}{m.group(2).upper()}"
    return s


def mapear(rel: str) -> tuple[str, str]:
    for padrao, alvo, obs in REGRAS:
        m = re.match(padrao, rel)
        if m:
            dest = alvo(m) if callable(alvo) else alvo
            pacote = dest.startswith(OC)
            return "/".join(limpar(s, pacote) for s in f"{RAIZ_NOVA}/{dest}".split("/")), obs
    return "", "sem regra — DEFINIR"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--banco", help="banco alternativo (padrao: config)")
    args = ap.parse_args()
    cfg = ler_config()
    exigir_fase_analise(cfg)
    banco = Path(args.banco) if args.banco else caminho_banco(cfg)
    saida = Path(cfg["saida"]) / "parceiro"
    saida.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f"file:{banco.as_posix()}?mode=ro", uri=True)
    data_cat = con.execute("SELECT MAX(visto_em) FROM item_nuvem").fetchone()[0]

    def rel(c): return c[len(RAIZ5) + 1:]
    def escopo(c): return c.startswith(RAIZ5 + "/") and not rel(c).startswith(FORA)

    pastas = sorted((r[0] for r in con.execute(
        "SELECT caminho FROM item_nuvem WHERE eh_pasta=1 AND deletado=0") if escopo(r[0])),
        key=str.lower)
    direto, direto_b, sub = Counter(), Counter(), Counter()
    for pai, tam in con.execute(
            "SELECT pasta_pai, tamanho FROM item_nuvem WHERE eh_pasta=0 AND deletado=0"):
        if escopo(pai + "/") or pai == RAIZ5:
            direto[pai] += 1
            direto_b[pai] += tam or 0
            p = pai
            while p.startswith(RAIZ5):
                sub[p] += 1
                p = p.rsplit("/", 1)[0]

    linhas, arvore, arvore_b = [], Counter(), Counter()
    sem_regra = 0
    for p in pastas:
        if sub[p] == 0:
            linhas.append([p, "", "não levar (pasta vazia)", 0, 0, 0])
            continue
        dest, obs = mapear(rel(p))
        if not dest:
            sem_regra += 1
        linhas.append([p, dest, obs, direto[p], direto_b[p], sub[p]])
        if dest and direto[p]:
            arvore[dest] += direto[p]
            arvore_b[dest] += direto_b[p]
    with (saida / "PARCEIRO_MAPA_PASTAS.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["pasta_atual", "pasta_proposta", "observacao",
                    "arquivos_diretos", "bytes_diretos", "arquivos_na_subarvore"])
        w.writerows(linhas)

    # arvore proposta agregada ate o 4o nivel
    agg, agg_b = Counter(), Counter()
    for d, n in arvore.items():
        partes = d.split("/")
        for k in range(1, min(len(partes), 4) + 1):
            agg["/".join(partes[:k])] += n
            agg_b["/".join(partes[:k])] += arvore_b[d]
    quando = datetime.now(BRT).strftime("%d/%m/%Y %H:%M")
    md = ["# Estrutura proposta — `Coordenação Programa`", "",
          f"Gerado em {quando} · catálogo de `{data_cat}` · `scripts/310_parceiro_mapa.py`",
          "", "Árvore até o 4º nível, com arquivos e GiB de cada ramo "
          "(sem `06. CERTIFICADORA ENTREGA` e sem pastas vazias).", "", "```"]
    for k in sorted(agg, key=str.lower):
        nivel = k.count("/")
        nome = k.rsplit("/", 1)[-1]
        md.append(f"{'    ' * nivel}{nome}  [{num(agg[k])} arq · {num(agg_b[k] / GiB, 2)} GiB]")
    md += ["```", "", f"Pastas sem regra: {sem_regra}.", ""]
    (saida / "PARCEIRO_ESTRUTURA_PROPOSTA.md").write_text("\n".join(md), encoding="utf-8")

    print(f"catalogo {data_cat} · {len(pastas)} pastas no escopo · sem regra: {sem_regra}")
    print("\n".join(md[7:-3]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

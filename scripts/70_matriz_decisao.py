"""
70_matriz_decisao - E4: transforma E1+E2+E3 numa matriz de decisao.

E4 E SOMENTE ANALISE. Este script nao chama a API, nao apaga, nao move,
nao renomeia e nao decide. Le o SQLite e produz documentos de leitura.

As colunas de decisao (`status`) saem sempre como NAO ANALISADO. Preencher
APROVAR / NAO APROVAR / INVESTIGAR e trabalho humano.

Saidas:
    output/E4_MATRIZ_DECISAO.md
    output/E4_MATRIZ_DECISAO.csv
    output/RESUMO_REUNIAO_GESTAO.md

Uso:
    python scripts\\70_matriz_decisao.py
"""

from __future__ import annotations

import csv
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_util import caminho_banco, exigir_fase_analise, ler_config  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass

GiB = 1024**3

# Pastas cujo conteudo foi replicado para atender orgao de controle.
MARCA_JURIDICA = ("mp-estado", "mp_estado", "resposta mp", "presta", "ministerio",
                  "ministério", "auditoria", "tcu", "tce")

# Padroes de descarte, um a um (para a frente E)
PADROES_E = {
    "backup": ["backup"],
    "antigo/antiga/old": ["antigo", "antiga", "old"],
    "superado/obsoleto": ["superado", "superada", "obsoleto"],
    "copia": ["copia", "cópia"],
    "temporario": ["temp", "temporario", "temporário"],
    "rascunho/nao usar": ["rascunho", "nao usar", "descartado",
                          "versoes anteriores", "lixeira"],
}

RE_REV = re.compile(r"\bREV\s*-?\s*\d+", re.I)

CAB_CSV = ["id", "frente", "categoria", "conjunto_envolvido", "arquivos",
           "espaco_ocupado_bytes", "espaco_ocupado_gib",
           "espaco_recuperavel_bytes", "espaco_recuperavel_gib",
           "pct_da_quota", "motivo_da_aparicao", "evidencia_tecnica",
           "risco_documental", "risco_juridico", "reversibilidade",
           "quem_valida", "acao_possivel", "pre_requisito",
           "entra_no_total", "status"]


def ptbr(t: str) -> str:
    return t.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def num(n, c: int = 0) -> str:
    return ptbr(f"{n:,.{c}f}")


def gib(b) -> str:
    return f"{num((b or 0) / GiB, 1)} GiB"


def pct(p, t) -> str:
    return ptbr(f"{(p or 0) * 100 / t:.2f}") + "%" if t else "-"


# ----------------------------------------------------------------------

def prioridade(volume: int, r_doc: int, r_jur: int, validacao: int,
               reversivel: int, quota: int) -> float:
    """Pontuacao de apoio a discussao — NAO e autorizacao.

    Escalas 1..3, onde 1 e o mais favoravel. Volume pesa mais, mas risco
    juridico e o que mais penaliza: um volume grande com pendencia juridica
    nao deve subir na lista so por ser grande.
    """
    v = min(volume / quota * 100, 30) / 30          # 0..1 (peso do volume)
    return round(
        v * 40                                       # volume: ate 40 pontos
        - (r_doc - 1) * 10                           # risco documental
        - (r_jur - 1) * 18                           # risco juridico (pesa mais)
        - (validacao - 1) * 6                        # dificuldade de validar
        - (reversivel - 1) * 6,                      # reversibilidade
        1)


ROTULO = {1: "Baixo", 2: "Medio", 3: "Alto"}
ROT_REV = {1: "Alta", 2: "Parcial", 3: "Nenhuma"}
ROT_VAL = {1: "Simples", 2: "Media", 3: "Complexa"}


def coletar(con: sqlite3.Connection) -> tuple[list, dict]:
    con.execute("""CREATE TEMP TABLE dup AS
        SELECT quick_xor_hash h, COUNT(*) n, MAX(tamanho) t
          FROM item_nuvem WHERE eh_pasta=0 AND deletado=0
           AND quick_xor_hash IS NOT NULL AND tamanho>=102400
         GROUP BY quick_xor_hash HAVING COUNT(*)>1""")

    ctx: dict = {}
    ctx["quota"], ctx["lixeira"] = con.execute(
        "SELECT MAX(quota_usada), MAX(quota_lixeira) FROM biblioteca").fetchone()
    ctx["arquivos"], ctx["vivos"] = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem"
        " WHERE eh_pasta=0 AND deletado=0").fetchone()
    Q = ctx["quota"]
    linhas: list[dict] = []
    seq = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0, "F": 0}

    def add(frente, categoria, conjunto, arq, ocupado, recuperavel,
            motivo, evidencia, r_doc, r_jur, reversivel, validacao,
            quem, acao, prereq, somavel=True):
        """`somavel=False` marca linha de DETALHAMENTO: ela aprofunda um
        conjunto ja contado em outra linha e nao entra nos totais."""
        seq[frente] += 1
        linhas.append({
            "id": f"{frente}{seq[frente]:02d}", "frente": frente,
            "categoria": categoria, "conjunto": conjunto, "arquivos": arq,
            "ocupado": ocupado, "recuperavel": recuperavel,
            "motivo": motivo, "evidencia": evidencia,
            "r_doc": r_doc, "r_jur": r_jur, "reversivel": reversivel,
            "validacao": validacao, "quem": quem, "acao": acao,
            "prereq": prereq, "status": "NAO ANALISADO", "somavel": somavel,
            "prio": prioridade(recuperavel, r_doc, r_jur, validacao, reversivel, Q),
        })

    # ---------------- FRENTE A: historico de versoes -----------------
    ver_pastas = con.execute("""
        SELECT i.nivel1, i.nivel2, COUNT(*), SUM(v.bytes_antigas),
               SUM(v.bytes_atual), SUM(v.n_versoes)
          FROM versao_resumo v JOIN item_nuvem i
            ON i.drive_id=v.drive_id AND i.item_id=v.item_id
         WHERE v.erro IS NULL AND v.bytes_antigas>0 AND i.nivel2 IS NOT NULL
         GROUP BY i.nivel1, i.nivel2
        HAVING SUM(v.bytes_antigas) > 200*1024*1024
         ORDER BY SUM(v.bytes_antigas) DESC""").fetchall()
    for n1, n2, qtd, antigas, atual, nv in ver_pastas:
        jur = any(m in f"/{n1}/{n2}".lower() for m in MARCA_JURIDICA)
        add("A", "Historico de versoes", f"/{n1}/{n2}", qtd, atual + antigas, antigas,
            f"{qtd} arquivos acumulam {num(nv)} versoes nesta pasta",
            "GET /items/{id}/versions — soma das versoes menos a corrente",
            1, 3 if jur else 1, 3, 1 if not jur else 2,
            "Gestao + TI" if not jur else "Juridico + TI",
            "Limitar numero de versoes guardadas (configuracao da biblioteca)",
            "Confirmar ausencia de retencao legal / litigation hold")

    ver_arq = con.execute("""
        SELECT i.caminho, v.n_versoes, v.bytes_atual, v.bytes_antigas, i.item_id
          FROM versao_resumo v JOIN item_nuvem i
            ON i.drive_id=v.drive_id AND i.item_id=v.item_id
         WHERE v.erro IS NULL AND v.bytes_antigas > 800*1024*1024
         ORDER BY v.bytes_antigas DESC LIMIT 15""").fetchall()
    ctx["ver_arq"] = ver_arq
    for caminho, nv, atual, antigas, iid in ver_arq:
        add("A", "Versoes — arquivo isolado", caminho, 1, atual + antigas, antigas,
            f"arquivo unico com {nv} versoes guardadas",
            f"item_id={iid} · {nv} versoes · atual {gib(atual)}",
            1, 3 if any(m in caminho.lower() for m in MARCA_JURIDICA) else 1,
            3, 1, "Autor do documento + gestao",
            "Reduzir historico deste arquivo mantendo a versao atual",
            "Confirmar que revisoes anteriores nao sao necessarias",
            somavel=False)   # ja contado na linha da pasta correspondente

    ctx["ver_total"] = con.execute("""
        SELECT estrato, arquivos_total, bytes_total, arquivos_medidos,
               bytes_medidos, bytes_antigas_medidos FROM versao_estrato
         ORDER BY estrato""").fetchall()

    # ---------------- FRENTE B: lixeira -------------------------------
    add("B", "Lixeira", "Lixeira do site (1o estagio conhecido)", 0,
        ctx["lixeira"], ctx["lixeira"],
        "quota.deleted informado pelo servico",
        "GET /sites/{id}/drive?$select=quota -> campo 'deleted'",
        2, 1, 3, 2, "TI + gestao do contrato",
        "Esvaziar lixeira apos confirmar prazo de retencao",
        "Confirmar no Centro de Administracao se ha lixeira de 2o estagio "
        "e qual o prazo de retencao vigente")

    # ---------------- FRENTES C e D: duplicatas ------------------------
    grupos = con.execute("""
        SELECT i.quick_xor_hash, i.caminho, i.tamanho, i.criado_em,
               i.modificado_em, i.criado_por, i.item_id, i.nivel1, i.nivel2
          FROM item_nuvem i
         WHERE i.eh_pasta=0 AND i.deletado=0
           AND i.quick_xor_hash IN (SELECT h FROM dup)
         ORDER BY i.quick_xor_hash, COALESCE(i.criado_em,'9999')""").fetchall()

    por_hash: dict[str, list] = {}
    for r in grupos:
        por_hash.setdefault(r[0], []).append(r)

    # classificar cada copia excedente
    exc_rev: dict[str, list] = {}       # duplicata entre revisoes (REV00/REV01)
    exc_dev: dict[str, list] = {}       # interna ao Desenvolvimento, sem REV
    exc_jur: dict[str, list] = {}       # pasta de resposta a orgao
    exc_out: dict[str, list] = {}
    ctx["top_rev"], ctx["top_jur"] = [], []

    for h, copias in por_hash.items():
        for pos, c in enumerate(copias):
            if pos == 0:
                continue                 # a mais antiga fica
            caminho = c[1] or ""
            baixo = caminho.lower()
            chave = f"/{c[7]}/{c[8]}" if c[8] else f"/{c[7]}"
            if any(m in baixo for m in MARCA_JURIDICA):
                exc_jur.setdefault(chave, []).append(c)
            elif RE_REV.search(caminho) and RE_REV.search(copias[0][1] or ""):
                exc_rev.setdefault(chave, []).append(c)
            elif baixo.startswith("/3. desenvolvimento"):
                exc_dev.setdefault(chave, []).append(c)
            else:
                exc_out.setdefault(chave, []).append(c)

    def resume(d):
        return [(k, len(v), sum(x[2] or 0 for x in v))
                for k, v in sorted(d.items(), key=lambda kv: -sum(x[2] or 0 for x in kv[1]))]

    for chave, qtd, bts in resume(exc_rev)[:12]:
        if bts < 100 * 1024**2:
            continue
        add("C", "Duplicata entre revisoes (REV00/REV01/...)", chave, qtd, bts, bts,
            "mesmo arquivo repetido em pastas de revisao sucessivas do mesmo projeto",
            "quickXorHash identico; caminhos com marcador REV",
            1, 1, 1, 1, "Coordenacao de engenharia",
            "Manter uma copia e referenciar nas demais revisoes",
            "Definir qual revisao e a pasta de referencia")

    for chave, qtd, bts in resume(exc_dev)[:12]:
        if bts < 100 * 1024**2:
            continue
        add("C", "Duplicata interna ao projeto", chave, qtd, bts, bts,
            "mesmo arquivo em mais de um lugar dentro da area de desenvolvimento",
            "quickXorHash identico entre as copias",
            1, 1, 1, 1, "Coordenacao de engenharia",
            "Manter uma copia, conferindo quem acessa as demais",
            "Verificar se ha link/referencia apontando para a copia")

    for chave, qtd, bts in resume(exc_out)[:10]:
        if bts < 100 * 1024**2:
            continue
        add("C", "Duplicata em outras areas", chave, qtd, bts, bts,
            "mesmo arquivo replicado fora do fluxo de projeto",
            "quickXorHash identico entre as copias",
            2, 2, 1, 2, "Responsavel pela pasta",
            "Avaliar caso a caso qual copia permanece",
            "Identificar o dono da pasta e o uso atual")

    for chave, qtd, bts in resume(exc_jur)[:15]:
        add("D", "VALIDACAO JURIDICA OBRIGATORIA", chave, qtd, bts, bts,
            "material replicado para compor resposta a orgao de controle "
            "ou prestacao de contas",
            "quickXorHash identico ao arquivo de origem no projeto",
            2, 3, 1, 3, "Juridico + gestao do contrato",
            "NENHUMA sem parecer juridico previo",
            "Parecer sobre a necessidade de manter a entrega integra como "
            "protocolada")

    ctx["exc"] = {"rev": sum(x[2] for x in resume(exc_rev)),
                  "dev": sum(x[2] for x in resume(exc_dev)),
                  "jur": sum(x[2] for x in resume(exc_jur)),
                  "out": sum(x[2] for x in resume(exc_out))}
    ctx["exc_n"] = {"rev": sum(x[1] for x in resume(exc_rev)),
                    "dev": sum(x[1] for x in resume(exc_dev)),
                    "jur": sum(x[1] for x in resume(exc_jur)),
                    "out": sum(x[1] for x in resume(exc_out))}

    # exemplos concretos para o .md
    ctx["ex_rev"] = []
    for h, copias in sorted(por_hash.items(),
                            key=lambda kv: -(kv[1][0][2] or 0) * (len(kv[1]) - 1))[:200]:
        if len(ctx["ex_rev"]) >= 6:
            break
        if RE_REV.search(copias[-1][1] or "") and not any(
                m in (copias[-1][1] or "").lower() for m in MARCA_JURIDICA):
            ctx["ex_rev"].append((h, copias))
    ctx["ex_jur"] = []
    for h, copias in sorted(por_hash.items(),
                            key=lambda kv: -(kv[1][0][2] or 0) * (len(kv[1]) - 1))[:200]:
        if len(ctx["ex_jur"]) >= 5:
            break
        if any(any(m in (c[1] or "").lower() for m in MARCA_JURIDICA) for c in copias[1:]):
            ctx["ex_jur"].append((h, copias))

    # ---------------- FRENTE E: material superado ----------------------
    ctx["sup"] = []
    for rotulo, termos in PADROES_E.items():
        cond = " OR ".join(["LOWER(caminho) LIKE ?"] * len(termos))
        args = [f"%{t}%" for t in termos]
        qtd, bts = con.execute(
            f"""SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem
                 WHERE eh_pasta=0 AND deletado=0 AND ({cond})
                   AND (quick_xor_hash IS NULL
                        OR quick_xor_hash NOT IN (SELECT h FROM dup))""",
            args).fetchone()
        top = con.execute(
            f"""SELECT caminho, tamanho FROM item_nuvem
                 WHERE eh_pasta=0 AND deletado=0 AND ({cond})
                   AND (quick_xor_hash IS NULL
                        OR quick_xor_hash NOT IN (SELECT h FROM dup))
                 ORDER BY tamanho DESC LIMIT 3""", args).fetchall()
        ctx["sup"].append((rotulo, qtd, bts, top))
        if qtd:
            add("E", f"Material marcado como '{rotulo}'",
                f"caminhos contendo: {', '.join(termos)}", qtd, bts, bts,
                "o proprio caminho sinaliza material fora de uso",
                "busca textual no caminho; ja descontadas as duplicatas",
                3, 2, 2, 3, "Responsavel documental da area",
                "Revisar pasta a pasta e decidir o que tem valor de arquivo",
                "Confirmar com quem produziu que o material esta mesmo superado")

    # ---------------- FRENTE F: nao identificado -----------------------
    ver_tot = 0
    for est, arq_tot, b_tot, arq_med, b_med, antigas in ctx["ver_total"]:
        if not arq_med:
            continue
        ver_tot += antigas if arq_med >= arq_tot else (
            (antigas / b_med * b_tot) if b_med else 0)
    ctx["ver_soma"] = ver_tot
    nao_id = ctx["quota"] - ctx["vivos"] - ver_tot - (ctx["lixeira"] or 0)
    ctx["nao_id"] = nao_id
    add("F", "Nao identificado", "Parcela da cota sem origem atribuida", 0,
        nao_id, 0,
        "sobra entre a cota cobrada e tudo que foi possivel medir",
        "cota - arquivos vivos - versoes medidas - lixeira informada",
        1, 1, 1, 3, "TI / administrador do SharePoint",
        "NENHUMA — investigar antes de qualquer acao",
        "Relatorio de armazenamento do Centro de Administracao do "
        "SharePoint (lixeira de 2o estagio, metadados de lista)")

    linhas.sort(key=lambda x: -x["prio"])
    return linhas, ctx


# ----------------------------------------------------------------------

def gravar_csv(linhas: list, ctx: dict, caminho: Path) -> None:
    Q = ctx["quota"]
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(CAB_CSV)
        for r in linhas:
            w.writerow([
                r["id"], r["frente"], r["categoria"], r["conjunto"], r["arquivos"],
                r["ocupado"], ptbr(f"{r['ocupado']/GiB:.3f}"),
                r["recuperavel"], ptbr(f"{r['recuperavel']/GiB:.3f}"),
                ptbr(f"{r['recuperavel']*100/Q:.3f}"),
                r["motivo"], r["evidencia"],
                ROTULO[r["r_doc"]], ROTULO[r["r_jur"]], ROT_REV[r["reversivel"]],
                r["quem"], r["acao"], r["prereq"],
                "sim" if r["somavel"] else "nao (detalhamento)", r["status"]])


def gravar_md(linhas: list, ctx: dict, caminho: Path) -> None:
    Q = ctx["quota"]
    L: list[str] = []
    ad = L.append

    ad("# E4 — Matriz de decisão")
    ad("")
    ad(f"**Data:** {date.today():%d/%m/%Y} · **Fase:** análise · "
       "**nada foi executado**")
    ad("")
    ad("> **Este documento não autoriza nada.** Ele organiza o que a medição "
       "encontrou em conjuntos concretos, com o risco de cada um, para que a "
       "decisão seja tomada por quem responde pelo documento.")
    ad(">")
    ad("> A coluna **status** sai sempre como `NÃO ANALISADO`. Preencher "
       "`APROVAR`, `NÃO APROVAR` ou `INVESTIGAR` é trabalho humano.")
    ad("")
    ad("---")
    ad("")

    # TOP -------------------------------------------------------------
    ad("## TOP OPORTUNIDADES PARA DISCUSSÃO COM A GESTÃO")
    ad("")
    ad("Ordenado por uma pontuação de apoio que combina volume recuperável, "
       "risco documental, risco jurídico, facilidade de validação e "
       "reversibilidade. **É apoio à análise, não autorização.**")
    ad("")
    ad("| Prioridade | Frente | Espaço envolvido | Potencial recuperável | "
       "Risco | Quem valida | Decisão necessária |")
    ad("|---:|---|---:|---:|---|---|---|")
    for i, r in enumerate(
            [x for x in linhas if x["recuperavel"] > 0 and x["somavel"]][:15], 1):
        risco = f"doc {ROTULO[r['r_doc']]} / jur **{ROTULO[r['r_jur']]}**"
        ad(f"| {i} | {r['frente']} — {r['categoria']} | {gib(r['ocupado'])} | "
           f"**{gib(r['recuperavel'])}** | {risco} | {r['quem']} | {r['acao']} |")
    ad("")
    ad("### Como ler a pontuação")
    ad("")
    ad("Volume pesa, mas **risco jurídico pesa mais**: um conjunto grande com "
       "pendência jurídica não sobe na lista só por ser grande. Por isso a "
       "frente D aparece abaixo de conjuntos menores.")
    ad("")
    ad("> **Nuance importante sobre reversibilidade.** Apagar uma cópia "
       "duplicada é *reversível no conteúdo* — o arquivo idêntico continua "
       "existindo na cópia mantida. Já apagar uma versão antiga é "
       "**irreversível**: aquela versão deixa de existir. O documento atual "
       "não é afetado, mas o histórico não volta.")
    ad("")

    # resumo por frente ------------------------------------------------
    ad("## Resumo por frente")
    ad("")
    ad("| Frente | Conjuntos | Espaço envolvido | Potencial recuperável | "
       "% da cota | Total da frente (E3) | Cobertura |")
    ad("|---|---:|---:|---:|---:|---:|---:|")
    nomes = {"A": "A — Histórico de versões", "B": "B — Lixeira",
             "C": "C — Duplicatas internas",
             "D": "D — Resposta a órgão (jurídico)",
             "E": "E — Material possivelmente superado",
             "F": "F — Não identificado"}
    for f in "ABCDEF":
        itens = [x for x in linhas if x["frente"] == f]
        if not itens:
            continue
        soma = [x for x in itens if x["somavel"]]
        oc = sum(x["ocupado"] for x in soma)
        rec = sum(x["recuperavel"] for x in soma)
        det = len(itens) - len(soma)
        marca = f" (+{det} detalh.)" if det else ""
        universo = {"A": ctx["ver_soma"], "B": ctx["lixeira"],
                    "C": ctx["exc"]["rev"] + ctx["exc"]["dev"] + ctx["exc"]["out"],
                    "D": ctx["exc"]["jur"],
                    "E": sum(x[2] for x in ctx["sup"]),
                    "F": ctx["nao_id"]}[f]
        cob = ptbr(f"{rec*100/universo:.0f}") + "%" if universo else "-"
        ad(f"| {nomes[f]} | {len(soma)}{marca} | {gib(oc)} | {gib(rec)} | "
           f"{pct(rec, Q)} | {gib(universo)} | {cob} |")
    ad("")
    ad("> **Cobertura.** A matriz lista os **maiores conjuntos**, não cada "
       "arquivo. Onde a cobertura é menor que 100%, o restante do volume está "
       "pulverizado em conjuntos pequenos demais para virar item de decisão — "
       "aparece no total da frente, mas não como linha própria.")
    ad("")
    ad("> Linhas marcadas como *detalhamento* (arquivos individuais dentro de "
       "uma pasta já listada) **não entram nos totais** — elas aprofundam um "
       "conjunto já contado.")
    ad("")
    ad("> As frentes **não se somam** para um total único: a frente A "
       "(versões) está fora dos arquivos vivos, enquanto C, D e E estão "
       "dentro deles. Ver os cenários no diagnóstico executivo.")
    ad("")

    # frente A ---------------------------------------------------------
    ad("---")
    ad("")
    ad("## FRENTE A — Histórico de versões")
    ad("")
    ad(f"Total medido/estimado: **{gib(ctx['ver_soma'])}**.")
    ad("")
    ad("O SharePoint guarda **uma cópia inteira do arquivo a cada salvamento**. "
       "Reduzir histórico **não apaga o documento atual**.")
    ad("")
    ad("| Faixa | Arquivos | Consultados | Versões antigas | Base |")
    ad("|---|---:|---:|---:|---|")
    for est, arq_tot, b_tot, arq_med, b_med, antigas in ctx["ver_total"]:
        if not arq_med:
            continue
        completo = arq_med >= arq_tot
        total = antigas if completo else ((antigas / b_med * b_tot) if b_med else 0)
        ad(f"| {est} | {num(arq_tot)} | {num(arq_med)} | {gib(total)} | "
           f"{'**medido 100%**' if completo else 'estimado'} |")
    ad("")
    ad("### Arquivos que mais concentram histórico")
    ad("")
    ad("| Arquivo | Versões | Tamanho atual | Espaço do histórico | item_id |")
    ad("|---|---:|---:|---:|---|")
    for via, nv, atual, antigas, iid in ctx["ver_arq"][:12]:
        curto = via if len(via) < 80 else "…" + via[-77:]
        ad(f"| `{curto}` | **{nv}** | {gib(atual)} | **{gib(antigas)}** | `{iid}` |")
    ad("")
    ad("### Política futura possível (não executada)")
    ad("")
    ad("Limitar o número de versões guardadas por biblioteca. É **mudança de "
       "configuração**, não exclusão de documento. Hoje há arquivo com 148 "
       "versões. Qual limite é aceitável é decisão de gestão — o efeito "
       "retroativo sobre versões já existentes precisa ser confirmado com a TI.")
    ad("")

    # frente B ---------------------------------------------------------
    ad("---")
    ad("")
    ad("## FRENTE B — Lixeira")
    ad("")
    ad(f"- Volume conhecido: **{gib(ctx['lixeira'])}** "
       f"({pct(ctx['lixeira'], Q)} da cota)")
    ad("- Recuperação possível: o mesmo volume, se esvaziada")
    ad("")
    ad("**Limitação técnica do Microsoft Graph:** a lixeira não é detalhável "
       "pela API. O endpoint `/recycleBin` não existe na versão 1.0 (responde "
       "`400 BadRequest`). O único número disponível é o campo `quota.deleted`, "
       "que **pode não incluir a lixeira de segundo estágio** do site.")
    ad("")
    ad("**A confirmar no Centro de Administração do SharePoint:**")
    ad("")
    ad("1. Existe lixeira de segundo estágio e qual o volume dela?")
    ad("2. Qual o prazo de retenção vigente (padrão são 93 dias)?")
    ad("3. Há item na lixeira que ainda possa ser necessário?")
    ad("")
    ad("**A lixeira não foi esvaziada.**")
    ad("")

    # frente C ---------------------------------------------------------
    ad("---")
    ad("")
    ad("## FRENTE C — Duplicatas internas")
    ad("")
    ad("| Padrão | Cópias excedentes | Espaço |")
    ad("|---|---:|---:|")
    ad(f"| Repetição entre revisões (REV00/REV01/…) | {num(ctx['exc_n']['rev'])} | "
       f"**{gib(ctx['exc']['rev'])}** |")
    ad(f"| Outras repetições dentro do desenvolvimento | {num(ctx['exc_n']['dev'])} | "
       f"{gib(ctx['exc']['dev'])} |")
    ad(f"| Repetições em outras áreas | {num(ctx['exc_n']['out'])} | "
       f"{gib(ctx['exc']['out'])} |")
    ad("")
    ad("### O padrão dominante: anexos que viajam junto com a revisão")
    ad("")
    ad("Cada nova revisão de um projeto leva junto **todo o conjunto de "
       "anexos**, mesmo os que não mudaram. Um mosaico ou nuvem de pontos "
       "idêntico aparece em REV00, REV01, REV02 e REV03.")
    ad("")
    ad("### Exemplos concretos")
    ad("")
    for h, copias in ctx["ex_rev"][:4]:
        unit = copias[0][2] or 0
        rec = unit * (len(copias) - 1)
        ad(f"**`{copias[0][1].split('/')[-1]}`** — {len(copias)} cópias de "
           f"{gib(unit)} · recuperável **{gib(rec)}**")
        ad("")
        ad("| | Caminho | Criado | item_id |")
        ad("|---|---|---|---|")
        for i, c in enumerate(copias[:5]):
            marca = "**mais antiga**" if i == 0 else (
                "mais recente" if i == len(copias) - 1 else "cópia")
            ad(f"| {marca} | `{c[1]}` | {(c[3] or '')[:10]} | `{c[6]}` |")
        ad("")
        ad(f"Impressão digital: `{h}`")
        ad("")
    ad("> **Nenhuma cópia foi escolhida para remoção.** A tabela mostra a mais "
       "antiga e a mais recente porque a data indica a direção em que o "
       "arquivo foi copiado — não porque uma delas deva sair.")
    ad("")

    # frente D ---------------------------------------------------------
    ad("---")
    ad("")
    ad("## FRENTE D — Resposta a órgão / prestação de contas")
    ad("")
    ad(f"## ⚠️ VALIDAÇÃO JURÍDICA OBRIGATÓRIA — {gib(ctx['exc']['jur'])}")
    ad("")
    ad("**Este volume está deliberadamente fora do potencial operacional.**")
    ad("")
    ad(f"{num(ctx['exc_n']['jur'])} arquivos são cópias exatas de material que "
       "já existe na pasta do projeto, replicadas para compor respostas ao "
       "Ministério Público, prestações de contas e entregas formais.")
    ad("")
    ad("**Por que aparecem duplicados:** ao montar uma resposta, a equipe "
       "copiou o conjunto de projetos para dentro da pasta da resposta, "
       "preservando a estrutura entregue. Tecnicamente é cópia idêntica; "
       "documentalmente é o **protocolo do que foi entregue**.")
    ad("")
    ad("**Por que não propomos ação técnica:** uma entrega a órgão de controle "
       "pode precisar permanecer íntegra exatamente como protocolada. Remover "
       "arquivos dela, ainda que idênticos a outros, é decisão jurídica.")
    ad("")
    ad("### Onde estão")
    ad("")
    ad("| Pasta | Cópias excedentes | Espaço |")
    ad("|---|---:|---:|")
    for r in [x for x in linhas if x["frente"] == "D"][:12]:
        ad(f"| `{r['conjunto']}` | {num(r['arquivos'])} | {gib(r['ocupado'])} |")
    ad("")
    ad("**Pergunta para o jurídico:** a entrega protocolada precisa permanecer "
       "íntegra, ou basta o registro de que foi entregue, podendo o conteúdo "
       "ser referenciado à pasta de origem?")
    ad("")

    # frente E ---------------------------------------------------------
    ad("---")
    ad("")
    ad("## FRENTE E — Material possivelmente superado")
    ad("")
    ad("Valores **já descontados** de duplicatas, para não contar duas vezes.")
    ad("")
    ad("| Padrão no caminho | Arquivos | Espaço | Maior exemplo |")
    ad("|---|---:|---:|---|")
    for rotulo, qtd, bts, top in ctx["sup"]:
        ex = f"`{top[0][0][-55:]}`" if top else "-"
        ad(f"| **{rotulo}** | {num(qtd)} | {gib(bts)} | {ex} |")
    ad("")
    ad("> **Identificar não é recomendar exclusão.** O nome da pasta é "
       "indício, não prova. Uma pasta chamada \"backup\" pode guardar o único "
       "exemplar de algo; \"temp\" pode ter virado definitivo. Cada conjunto "
       "precisa de quem o produziu.")
    ad("")

    # frente F ---------------------------------------------------------
    ad("---")
    ad("")
    ad("## FRENTE F — Não identificado")
    ad("")
    ad(f"**{gib(ctx['nao_id'])}** ({pct(ctx['nao_id'], Q)} da cota) não foram "
       "atribuídos a nenhuma origem.")
    ad("")
    ad("**Já descartado por medição direta:**")
    ad("")
    ad("| Hipótese | Resultado |")
    ad("|---|---|")
    ad("| Preservation Hold Library (retenção) | não existe no site — `404` |")
    ad("| Sub-sites consumindo a mesma cota | nenhum |")
    ad("| Outra biblioteca com conteúdo | `Documentos` tem 3,2 MB |")
    ad("| Faixas pequenas subestimadas | testado com amostra 4×: não confirmou |")
    ad("")
    ad("**Hipóteses ainda possíveis:**")
    ad("")
    ad("1. **Lixeira de segundo estágio** não contabilizada em `quota.deleted`")
    ad("2. **Versões de arquivos que já estão na lixeira** — não aparecem no "
       "catálogo de itens vivos")
    ad("3. **Overhead de metadados/índice** dos 160 mil itens de lista")
    ad("4. **Margem da extrapolação** das faixas abaixo de 10 MB")
    ad("")
    ad("**Informação administrativa necessária para fechar a conta:** o "
       "relatório de armazenamento do **Centro de Administração do "
       "SharePoint**, que discrimina lixeira de 1º e 2º estágio e versões — "
       "dados que a API pública não expõe.")
    ad("")
    ad("> Enquanto não fechar, esta parcela **não deve ser prometida como "
       "recuperável**.")
    ad("")

    # matriz completa --------------------------------------------------
    ad("---")
    ad("")
    ad("## Matriz completa")
    ad("")
    ad(f"{len(linhas)} conjuntos. Versão completa com todas as colunas em "
       "`output/E4_MATRIZ_DECISAO.csv`.")
    ad("")
    ad("| ID | Frente | Conjunto | Arq. | Recuperável | No total? | R.doc | "
       "R.jur | Revers. | Quem valida | Status |")
    ad("|---|---|---|---:|---:|---|---|---|---|---|---|")
    for r in linhas:
        conj = r["conjunto"] if len(r["conjunto"]) < 58 else "…" + r["conjunto"][-55:]
        ad(f"| `{r['id']}` | {r['frente']} | `{conj}` | {num(r['arquivos'])} | "
           f"{gib(r['recuperavel'])} | {'sim' if r['somavel'] else '*detalhe*'} | "
           f"{ROTULO[r['r_doc']]} | {ROTULO[r['r_jur']]} | "
           f"{ROT_REV[r['reversivel']]} | {r['quem']} | **{r['status']}** |")
    ad("")
    ad("---")
    ad("")
    ad("## Nada foi executado")
    ad("")
    ad("- Nenhum arquivo foi excluído")
    ad("- Nenhuma versão foi excluída")
    ad("- Nenhuma lixeira foi esvaziada")
    ad("- Nenhum arquivo foi movido ou renomeado")
    ad("- Somente `GET` no Microsoft Graph")
    ad("- **E5 não foi executada**")
    ad("")
    caminho.write_text("\n".join(L), encoding="utf-8")


def gravar_resumo(linhas: list, ctx: dict, caminho: Path) -> None:
    Q, V = ctx["quota"], ctx["vivos"]
    dup_rec = ctx["exc"]["rev"] + ctx["exc"]["dev"] + ctx["exc"]["out"]
    L: list[str] = []
    ad = L.append

    ad("# Programa — SharePoint: resumo para reunião")
    ad("")
    ad(f"**{date.today():%d/%m/%Y}** · Emmily Maciel · TI do contrato")
    ad("")
    ad("---")
    ad("")
    ad("## 1. Quanto o SharePoint usa hoje")
    ad("")
    ad(f"# {gib(Q)}")
    ad("")
    ad("É o que a Microsoft cobra do contrato.")
    ad("")
    ad("## 2. Onde está o espaço")
    ad("")
    ad("| | Espaço | % |")
    ad("|---|---:|---:|")
    ad(f"| Arquivos que as pessoas veem | {gib(V)} | {pct(V, Q)} |")
    ad(f"| Versões antigas dos mesmos arquivos | {gib(ctx['ver_soma'])} | "
       f"{pct(ctx['ver_soma'], Q)} |")
    ad(f"| Lixeira | {gib(ctx['lixeira'])} | {pct(ctx['lixeira'], Q)} |")
    ad(f"| Ainda não identificado | {gib(ctx['nao_id'])} | {pct(ctx['nao_id'], Q)} |")
    ad("")
    ad("## 3. Quanto são arquivos atuais")
    ad("")
    ad(f"**{gib(V)}** em **{num(ctx['arquivos'])} arquivos**.")
    ad("")
    ad("## 4. Quanto são versões antigas")
    ad("")
    ad(f"**{gib(ctx['ver_soma'])}**")
    ad("")
    ad("Cada vez que alguém salva um arquivo, o SharePoint guarda uma cópia "
       "inteira dele. Um relatório de 85 MB salvo 41 vezes ocupa mais de 3 GB, "
       "mas a pessoa vê **um** arquivo. Há documento com **148 versões** "
       "guardadas.")
    ad("")
    ad("## 5. Quanto são duplicatas")
    ad("")
    ad(f"**{gib(dup_rec + ctx['exc']['jur'])}** em cópias exatas — o mesmo "
       "arquivo guardado em mais de um lugar.")
    ad("")
    ad("Divide-se em duas situações muito diferentes:")
    ad("")
    ad("| | Espaço | Situação |")
    ad("|---|---:|---|")
    ad(f"| Cópias internas dos projetos | {gib(dup_rec)} | pode ser estudado |")
    ad(f"| Cópias em respostas ao MP / prestação de contas | "
       f"{gib(ctx['exc']['jur'])} | **depende do jurídico** |")
    ad("")
    ad("## 6. Quanto é lixeira")
    ad("")
    ad(f"**{gib(ctx['lixeira'])}** — bem menos do que se imaginava.")
    ad("")
    ad("## 7. O que pode ser estudado sem eliminar documentos atuais")
    ad("")
    ad(f"### {gib(ctx['ver_soma'] + ctx['lixeira'])}")
    ad("")
    ad("Reduzir o histórico de versões e esvaziar a lixeira. **Nenhum "
       "documento deixa de existir** — a versão atual de cada arquivo "
       "permanece intacta.")
    ad("")
    ad("Único efeito: deixa de ser possível voltar a uma versão anterior.")
    ad("")
    ad("## 8. O que depende de revisão documental")
    ad("")
    ad(f"### {gib(dup_rec)}")
    ad("")
    ad("Cópias exatas dentro dos projetos — o mesmo anexo repetido em cada "
       "revisão (REV00, REV01, REV02…). Precisa de alguém da engenharia "
       "confirmar qual pasta é a de referência.")
    ad("")
    ad(f"Mais **{gib(sum(x[2] for x in ctx['sup']))}** em pastas com nome de "
       "*backup*, *antigo*, *superado*, *temp* — que precisam de quem "
       "produziu o material.")
    ad("")
    ad("## 9. O que depende obrigatoriamente do jurídico")
    ad("")
    ad(f"### {gib(ctx['exc']['jur'])}")
    ad("")
    ad("Material copiado para responder ao Ministério Público e para "
       "prestações de contas. São cópias idênticas ao que já existe nos "
       "projetos, **mas são o protocolo do que foi entregue**.")
    ad("")
    ad("**É o maior volume isolado e o de maior risco.** Não entra em nenhum "
       "plano até o jurídico se manifestar.")
    ad("")
    ad("## 10. Decisões que precisamos da gestão")
    ad("")
    ad("1. **Podemos reduzir o histórico de versões?** Existe alguma "
       "obrigação legal ou contratual de manter todas as versões?")
    ad("2. **Quem consulta o jurídico** sobre o material de resposta ao MP?")
    ad("3. **Qual pasta é a oficial** quando o mesmo arquivo está no projeto "
       "e na pasta de entrega?")
    ad("4. **Quem valida** o material marcado como backup/antigo em cada área?")
    ad("5. **Qual limite de versões** passa a valer daqui para frente?")
    ad("")
    ad("---")
    ad("")
    ad("## Ações possíveis")
    ad("")
    ad("| AÇÃO POSSÍVEL | ESPAÇO POTENCIAL | RISCO | QUEM PRECISA APROVAR | EXECUTADA? |")
    ad("|---|---:|---|---|---|")
    ad(f"| Reduzir histórico de versões | {gib(ctx['ver_soma'])} | Baixo — não "
       f"apaga documento | Gestão + TI (após checar retenção legal) | **NÃO** |")
    ad(f"| Esvaziar lixeira | {gib(ctx['lixeira'])} | Baixo | TI + gestão | **NÃO** |")
    ad(f"| Remover cópias repetidas entre revisões | {gib(ctx['exc']['rev'])} | "
       f"Médio | Coordenação de engenharia | **NÃO** |")
    ad(f"| Remover outras cópias internas | {gib(ctx['exc']['dev'] + ctx['exc']['out'])} | "
       f"Médio | Responsável de cada área | **NÃO** |")
    ad(f"| Revisar material backup/antigo/superado | "
       f"{gib(sum(x[2] for x in ctx['sup']))} | Alto | Responsável documental | **NÃO** |")
    ad(f"| Tratar cópias de resposta ao MP | {gib(ctx['exc']['jur'])} | "
       f"**Alto — jurídico** | Jurídico + gestão do contrato | **NÃO** |")
    ad(f"| Investigar parcela não identificada | {gib(ctx['nao_id'])} | — | "
       f"TI (Centro de Administração) | **NÃO** |")
    ad("")
    ad("---")
    ad("")
    ad("## Situação atual do trabalho")
    ad("")
    ad("O diagnóstico foi feito **inteiramente por leitura**. Até este momento:")
    ad("")
    ad("- ❌ **Nenhum arquivo foi excluído**")
    ad("- ❌ **Nenhuma versão foi excluída**")
    ad("- ❌ **Nenhuma lixeira foi esvaziada**")
    ad("- ❌ **Nenhum arquivo foi movido**")
    ad("- ❌ **Nenhum arquivo foi renomeado**")
    ad("- ❌ **E5 (execução) não foi executada**")
    ad("")
    ad("Nada será alterado no SharePoint sem aprovação por escrito.")
    ad("")
    caminho.write_text("\n".join(L), encoding="utf-8")


def main() -> int:
    cfg = ler_config()
    exigir_fase_analise(cfg)
    saida = Path(cfg["saida"])
    con = sqlite3.connect(caminho_banco(cfg))
    try:
        linhas, ctx = coletar(con)
        gravar_csv(linhas, ctx, saida / "E4_MATRIZ_DECISAO.csv")
        gravar_md(linhas, ctx, saida / "E4_MATRIZ_DECISAO.md")
        gravar_resumo(linhas, ctx, saida / "RESUMO_REUNIAO_GESTAO.md")

        print("=" * 68)
        print("E4 — MATRIZ DE DECISAO (somente analise)")
        print("=" * 68)
        print(f"  conjuntos mapeados ......... {len(linhas)}")
        for f, nome in (("A", "versoes"), ("B", "lixeira"), ("C", "dup internas"),
                        ("D", "resposta a orgao"), ("E", "superado"),
                        ("F", "nao identificado")):
            it = [x for x in linhas if x["frente"] == f and x["somavel"]]
            det = len([x for x in linhas if x["frente"] == f]) - len(it)
            if it:
                print(f"    frente {f} ({nome:<17}) {len(it):>3} conjuntos"
                      f"{f' +{det} detalh.' if det else '':>12} · "
                      f"{gib(sum(x['recuperavel'] for x in it))}")
        print()
        print(f"  status preenchido .......... NAO ANALISADO (100%)")
        print()
        for p in ("E4_MATRIZ_DECISAO.md", "E4_MATRIZ_DECISAO.csv",
                  "RESUMO_REUNIAO_GESTAO.md"):
            print(f"  {saida / p}")
        print()
        print("  Nada foi executado. E5 nao iniciada.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())

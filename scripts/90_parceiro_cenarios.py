"""
90_parceiro_cenarios - analise especial das pastas 5 e 6, intersecoes e cenarios.

Somente leitura. Nao chama a API, nao apaga, nao move, nao decide.

Premissa de negocio informada pela Emmily (nao medida por este script):
    `6. Arquivos PARCEIRO` e uma COPIA de acervo cuja origem esta FORA do
    SharePoint Programa. Por isso NAO se tenta provar redundancia dela por
    hash interno — a pergunta e o custo de manter a copia aqui.

Distincao entre as duas pastas:
    5. Compartilhado PARCEIRO -> area de TRANSFERENCIA (ja foi recebido?)
    6. Arquivos PARCEIRO      -> COPIA DE ACERVO EXTERNO (precisa ficar?)

Todos os cenarios usam conjuntos de item_id, de modo que o potencial
INCREMENTAL nunca conte o mesmo arquivo duas vezes.

Saidas:
    output/ANALISE_PASTA5_COMPARTILHADO_PARCEIRO.md
    output/ANALISE_PASTA6_ARQUIVOS_PARCEIRO.md
    output/MATRIZ_INTERSECOES.md
    data/cenarios.json

Uso:
    python scripts\\90_parceiro_cenarios.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_util import caminho_banco, exigir_fase_analise, ler_config  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass

GiB, GB = 1024**3, 1000**3
BRT = timezone(timedelta(hours=-3))
P5, P6 = "5. Compartilhado PARCEIRO", "6. Arquivos PARCEIRO"
MARCA_JUR = ("mp-estado", "mp_estado", "resposta mp", "presta", "ministerio",
             "ministério", "auditoria", "tcu", "tce")
RE_REV = re.compile(r"\bREV\s*-?\s*\d+", re.I)
PESADAS = (".zip", ".rar", ".7z", ".dav", ".mp4", ".mov", ".avi",
           ".iso", ".bak", ".pbix")
PADROES_SUP = ["backup", "superado", "superada", "obsoleto", "antigo", "antiga",
               "old", "lixeira", "descartado", "versoes anteriores", "nao usar",
               "rascunho", "temp", "temporario", "temporário", "copia", "cópia"]


def ptbr(t): return t.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
def num(n, c=0): return ptbr(f"{n:,.{c}f}")
def gib(b): return f"{num((b or 0)/GiB, 1)} GiB"
def gbd(b): return f"{num((b or 0)/GB, 1)} GB"
def pct(p, t, c=1): return ptbr(f"{(p or 0)*100/t:.{c}f}") + "%" if t else "-"


def classificar(cam, cam_origem):
    b = (cam or "").lower()
    if any(m in b for m in MARCA_JUR): return "juridico"
    if b.startswith("/6. arquivos parceiro"): return "pasta6"
    if b.startswith("/5. compartilhado parceiro"): return "pasta5"
    if RE_REV.search(cam or "") and RE_REV.search(cam_origem or ""): return "revisoes"
    if b.startswith("/3. desenvolvimento"): return "desenvolvimento"
    return "outras"


def main() -> int:
    cfg = ler_config()
    exigir_fase_analise(cfg)
    saida = Path(cfg["saida"])
    con = sqlite3.connect(caminho_banco(cfg))
    agora = datetime.now(BRT)
    quando = f"{agora:%d/%m/%Y %H:%M}"
    try:
        Q, LIX = con.execute(
            "SELECT MAX(quota_usada), MAX(quota_lixeira) FROM biblioteca").fetchone()
        NARQ, V = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem"
            " WHERE eh_pasta=0 AND deletado=0").fetchone()
        tam = dict(con.execute(
            "SELECT item_id, tamanho FROM item_nuvem WHERE eh_pasta=0 AND deletado=0"))

        # ---- grupos e excedentes ------------------------------------
        linhas = con.execute("""
            SELECT quick_xor_hash, item_id, caminho, tamanho, nivel1
              FROM item_nuvem
             WHERE eh_pasta=0 AND deletado=0 AND quick_xor_hash IN (
                SELECT quick_xor_hash FROM item_nuvem
                 WHERE eh_pasta=0 AND deletado=0 AND quick_xor_hash IS NOT NULL
                   AND tamanho>=102400
                 GROUP BY quick_xor_hash HAVING COUNT(*)>1)
             ORDER BY quick_xor_hash, COALESCE(criado_em,'9999')""").fetchall()
        por_hash = {}
        for r in linhas:
            por_hash.setdefault(r[0], []).append(r)
        exc = {}
        for h, cop in por_hash.items():
            for pos, c in enumerate(cop):
                if pos:
                    exc[c[1]] = classificar(c[2], cop[0][2])

        def ids_classe(*classes):
            return {k for k, v in exc.items() if v in classes}

        # ---- versoes -------------------------------------------------
        faixas = con.execute("""
            SELECT estrato, arquivos_total, bytes_total, arquivos_medidos,
                   bytes_medidos, bytes_antigas_medidos FROM versao_estrato
             ORDER BY estrato""").fetchall()
        VER = 0
        for est, at, bt, am, bm, ant in faixas:
            if not am:
                continue
            VER += ant if am >= at else ((ant / bm * bt) if bm else 0)
        NAO_ID = Q - V - VER - (LIX or 0)

        # ============ PASTA 5 =========================================
        p5_ids = {r[0] for r in con.execute(
            "SELECT item_id FROM item_nuvem WHERE eh_pasta=0 AND deletado=0"
            " AND nivel1=?", (P5,))}
        p5_tot = sum(tam[i] for i in p5_ids)
        p5_arq = len(p5_ids)
        p5_sub = con.execute(
            "SELECT COUNT(*) FROM item_nuvem WHERE eh_pasta=1 AND deletado=0"
            " AND nivel1=? AND profundidade>1", (P5,)).fetchone()[0]

        # hash que tambem existe FORA da pasta 5
        fora5, so5 = 0, 0
        n_fora5 = n_so5 = 0
        for h, cop in por_hash.items():
            dentro = [c for c in cop if c[4] == P5]
            if not dentro:
                continue
            tem_fora = any(c[4] != P5 for c in cop)
            for c in dentro:
                if tem_fora:
                    fora5 += c[3] or 0
                    n_fora5 += 1
                else:
                    so5 += c[3] or 0
                    n_so5 += 1
        sem_grupo5 = p5_tot - fora5 - so5          # arquivos unicos no acervo todo
        p5_exc_total = sum(tam[i] for i in p5_ids & set(exc))
        p5_exc_jur = sum(tam[i] for i in p5_ids & ids_classe("juridico"))
        p5_exc_p5 = sum(tam[i] for i in p5_ids & ids_classe("pasta5"))

        # ============ PASTA 6 =========================================
        p6_ids = {r[0] for r in con.execute(
            "SELECT item_id FROM item_nuvem WHERE eh_pasta=0 AND deletado=0"
            " AND nivel1=?", (P6,))}
        p6_tot = sum(tam[i] for i in p6_ids)
        p6_arq = len(p6_ids)
        p6_sub = con.execute(
            "SELECT COUNT(*) FROM item_nuvem WHERE eh_pasta=1 AND deletado=0"
            " AND nivel1=? AND profundidade>1", (P6,)).fetchone()[0]
        p6_exc = sum(tam[i] for i in p6_ids & set(exc))
        p6_ver = con.execute("""
            SELECT COALESCE(SUM(v.bytes_antigas),0), COUNT(*) FROM versao_resumo v
              JOIN item_nuvem i ON i.drive_id=v.drive_id AND i.item_id=v.item_id
             WHERE v.erro IS NULL AND i.nivel1=?""", (P6,)).fetchone()
        p6_sub_top = con.execute("""
            SELECT nivel2, COUNT(*), SUM(tamanho) FROM item_nuvem
             WHERE eh_pasta=0 AND deletado=0 AND nivel1=? AND nivel2 IS NOT NULL
             GROUP BY nivel2 ORDER BY SUM(tamanho) DESC LIMIT 20""", (P6,)).fetchall()
        p6_arq_top = con.execute("""
            SELECT caminho, tamanho, item_id FROM item_nuvem
             WHERE eh_pasta=0 AND deletado=0 AND nivel1=?
             ORDER BY tamanho DESC LIMIT 20""", (P6,)).fetchall()
        p6_ext = con.execute("""
            SELECT COALESCE(extensao,'(sem)'), COUNT(*), SUM(tamanho) FROM item_nuvem
             WHERE eh_pasta=0 AND deletado=0 AND nivel1=?
             GROUP BY extensao ORDER BY SUM(tamanho) DESC LIMIT 12""", (P6,)).fetchall()
        p5_sub_top = con.execute("""
            SELECT nivel2, COUNT(*), SUM(tamanho) FROM item_nuvem
             WHERE eh_pasta=0 AND deletado=0 AND nivel1=? AND nivel2 IS NOT NULL
             GROUP BY nivel2 ORDER BY SUM(tamanho) DESC LIMIT 20""", (P5,)).fetchall()

        # ---- material superado (precisa vir antes dos cenarios) ------
        _cond = "(" + " OR ".join(["LOWER(caminho) LIKE ?"] * len(PADROES_SUP)) + ")"
        sup_ids_pre = {r[0] for r in con.execute(
            f"SELECT item_id FROM item_nuvem WHERE eh_pasta=0 AND deletado=0"
            f" AND {_cond}", [f"%{p}%" for p in PADROES_SUP])}

        # ============ CENARIOS INCREMENTAIS ===========================
        ja = set()                      # item_id ja contados
        cen = []

        def incremental(rotulo, ids, extra_bytes=0, nota=""):
            novos = ids - ja
            b = sum(tam.get(i, 0) for i in novos) + extra_bytes
            ja.update(novos)
            cen.append({"rotulo": rotulo, "bytes": b, "arquivos": len(novos),
                        "nota": nota})
            return b

        a = incremental("A — versões + lixeira", set(), VER + (LIX or 0),
                        "não são arquivos vivos; espaço separado")
        b = incremental("B — duplicatas internas (revisões/desenvolvimento/outras)",
                        ids_classe("revisoes", "desenvolvimento", "outras"))
        c = incremental("C — Compartilhado PARCEIRO (cópias excedentes)",
                        ids_classe("pasta5"))
        d = incremental("D — Arquivos PARCEIRO (retirada da cópia do acervo)", p6_ids)
        e = incremental("E — material superado (backup/antigo/temp)", sup_ids_pre)
        j = incremental("JURÍDICO — respostas a órgão (separado)",
                        ids_classe("juridico"))

        # ============ INTERSECOES =====================================
        cond = "(" + " OR ".join(["LOWER(caminho) LIKE ?"] * len(PADROES_SUP)) + ")"
        sup_ids = {r[0] for r in con.execute(
            f"SELECT item_id FROM item_nuvem WHERE eh_pasta=0 AND deletado=0"
            f" AND {cond}", [f"%{p}%" for p in PADROES_SUP])}
        pes_ids = {r[0] for r in con.execute(
            f"SELECT item_id FROM item_nuvem WHERE eh_pasta=0 AND deletado=0"
            f" AND extensao IN ({','.join('?'*len(PESADAS))})", PESADAS)}
        conj = {
            "Dup. internas (exc.)": ids_classe("revisoes", "desenvolvimento", "outras"),
            "Dup. jurídicas (exc.)": ids_classe("juridico"),
            "Dup. pasta 5 (exc.)": ids_classe("pasta5"),
            "Dup. pasta 6 (exc.)": ids_classe("pasta6"),
            "Material superado": sup_ids,
            "Arquivos pesados": pes_ids,
            "Pasta 5 (tudo)": p5_ids,
            "Pasta 6 (tudo)": p6_ids,
        }

        # ---------------- MD: intersecoes -----------------------------
        L = [f"# Matriz de interseções entre categorias", "",
             f"**Posição: {quando}** · somente leitura", "",
             "Cada célula mostra o **espaço em comum** entre a categoria da linha "
             "e a da coluna. A diagonal é o total da própria categoria.", "",
             "> Serve para responder: *“se eu somar duplicatas + material "
             "superado + arquivos pesados, quanto estou contando duas vezes?”*", ""]
        nomes = list(conj)
        L.append("| Categoria | Total | " + " | ".join(n[:16] for n in nomes) + " |")
        L.append("|---|---:|" + "---:|" * len(nomes))
        for a_ in nomes:
            cel = []
            for b_ in nomes:
                v = sum(tam.get(i, 0) for i in conj[a_] & conj[b_])
                cel.append(gib(v) if v else "—")
            tot_a = sum(tam.get(i, 0) for i in conj[a_])
            L.append(f"| **{a_}** | **{gib(tot_a)}** | " + " | ".join(cel) + " |")
        L.append("")
        L.append("## Material superado — onde está")
        L.append("")
        L.append("| Situação | Espaço | Já contado em outra categoria? |")
        L.append("|---|---:|---|")
        s_int = sum(tam.get(i, 0) for i in sup_ids & conj["Dup. internas (exc.)"])
        s_jur = sum(tam.get(i, 0) for i in sup_ids & conj["Dup. jurídicas (exc.)"])
        s_p5 = sum(tam.get(i, 0) for i in sup_ids & p5_ids)
        s_p6 = sum(tam.get(i, 0) for i in sup_ids & p6_ids)
        s_tot = sum(tam.get(i, 0) for i in sup_ids)
        s_livre = sum(tam.get(i, 0) for i in sup_ids - set(exc) - p5_ids - p6_ids)
        L.append(f"| Dentro das duplicatas internas | {gib(s_int)} | **sim** |")
        L.append(f"| Dentro das duplicatas jurídicas | {gib(s_jur)} | **sim** |")
        L.append(f"| Dentro da pasta 5 | {gib(s_p5)} | **sim** |")
        L.append(f"| Dentro da pasta 6 | {gib(s_p6)} | **sim** |")
        L.append(f"| **Fora de qualquer duplicata e fora das pastas 5/6** | "
                 f"**{gib(s_livre)}** | **não — é incremental** |")
        L.append(f"| *Total identificado como superado* | *{gib(s_tot)}* | |")
        L.append("")
        L.append("> Dos " + gib(s_tot) + " marcados como superados, apenas "
                 f"**{gib(s_livre)}** acrescentam espaço novo ao potencial. "
                 "O resto já está contado em outra frente.")
        L.append("")
        (saida / "MATRIZ_INTERSECOES.md").write_text("\n".join(L), encoding="utf-8")

        # ---------------- MD: pasta 5 ---------------------------------
        L = [f"# `5. Compartilhado PARCEIRO` — análise especial", "",
             f"**Posição: {quando}** · somente leitura · **nada foi excluído**", "",
             "> **Natureza da pasta:** área de **compartilhamento/transferência** "
             "com o PARCEIRO. A pergunta não é se o conteúdo é redundante, e sim: "
             "*os conjuntos já foram integralmente recebidos pelo PARCEIRO e ainda "
             "precisam permanecer aqui?*", "",
             "## Números", "", "| | |", "|---|---:|",
             f"| Tamanho total | **{gib(p5_tot)}** / {gbd(p5_tot)} / {num(p5_tot)} bytes |",
             f"| % dos arquivos vivos | {pct(p5_tot, V)} |",
             f"| % da quota | {pct(p5_tot, Q)} |",
             f"| Arquivos | {num(p5_arq)} |",
             f"| Subpastas | {num(p5_sub)} |", ""]
        L += ["## Quanto existe também em outro lugar do Programa (por hash)", "",
              "| Situação | Arquivos | Espaço | % da pasta |", "|---|---:|---:|---:|",
              f"| Conteúdo que **também existe fora** da pasta 5 | {num(n_fora5)} | "
              f"**{gib(fora5)}** | {pct(fora5, p5_tot)} |",
              f"| Duplicado **apenas dentro** da própria pasta 5 | {num(n_so5)} | "
              f"{gib(so5)} | {pct(so5, p5_tot)} |",
              f"| **Exclusivo** (sem cópia em lugar nenhum) | "
              f"{num(p5_arq - n_fora5 - n_so5)} | **{gib(sem_grupo5)}** | "
              f"{pct(sem_grupo5, p5_tot)} |", "",
              f"**{pct(fora5, p5_tot)} do conteúdo da pasta 5 já existe em outro "
              "ponto do SharePoint Programa**, comprovado por impressão digital idêntica.",
              "",
              "## Composição das cópias excedentes dentro da pasta 5", "",
              "| Classe | Espaço |", "|---|---:|",
              f"| Material de resposta a órgão / prestação de contas | {gib(p5_exc_jur)} |",
              f"| Transferência ao PARCEIRO propriamente dita | {gib(p5_exc_p5)} |",
              f"| **Total de excedentes na pasta** | **{gib(p5_exc_total)}** |", "",
              "> ⚠️ **Leitura importante:** a maior parte da redundância da pasta 5 "
              "**não é material de transferência ao PARCEIRO** — é material de "
              "**resposta a órgão de controle** guardado dentro dela. Esse volume "
              "segue a regra da frente jurídica, não a do PARCEIRO.", "",
              "## TOP 20 subpastas", "",
              "| Subpasta | Arquivos | Tamanho | % da pasta |", "|---|---:|---:|---:|"]
        for n2, qtd, bts in p5_sub_top:
            L.append(f"| `{n2}` | {num(qtd)} | {gib(bts)} | {pct(bts, p5_tot)} |")
        L += ["", "## Cenário CONDICIONAL — não é recomendação", "",
              "> **Se** o responsável pelo PARCEIRO — incluindo **Eliseu**, quando "
              "aplicável — confirmar **formalmente e por escrito** que determinado "
              "conjunto já foi **integralmente recebido/baixado** e que a "
              "manutenção da cópia no SharePoint Programa não é necessária, o "
              "potencial de liberação correspondente seria:", "",
              f"### até {gib(p5_exc_p5)} (cópias excedentes de transferência)",
              "",
              f"### até {gib(fora5)} (todo conteúdo que já existe fora da pasta 5)",
              "",
              "O segundo número é o teto teórico e **inclui material jurídico**, "
              "que tem regra própria. O primeiro é o que efetivamente pertence à "
              "frente PARCEIRO.", "",
              "**Condições que precisam ser satisfeitas antes de qualquer ação:**", "",
              "1. Confirmação formal do PARCEIRO de recebimento integral do conjunto",
              "2. Confirmação de que a cópia no Programa não é exigida por contrato",
              "3. Parecer sobre o material de resposta a órgão guardado na pasta",
              "4. Definição de quem responde pela pasta do lado do Programa", "",
              "**VALIDAÇÃO NECESSÁRIA:** `PARCEIRO / responsável documental / gestão "
              "do contrato` e, quando aplicável, `Confirmação de Eliseu de que o "
              "conjunto foi integralmente recebido/baixado`.", "",
              "**Confirmação verbal não é suficiente.**", "",
              "**EXECUTADO? NÃO.**", ""]
        (saida / "ANALISE_PASTA5_COMPARTILHADO_PARCEIRO.md").write_text(
            "\n".join(L), encoding="utf-8")

        # ---------------- MD: pasta 6 ---------------------------------
        L = [f"# `6. Arquivos PARCEIRO` — análise especial", "",
             f"**Posição: {quando}** · somente leitura · **nada foi excluído**", "",
             "## Premissa de negócio (informada pela gestão, não medida)", "",
             "> `6. Arquivos PARCEIRO` contém uma **cópia de arquivos cuja origem está "
             "fora deste SharePoint Programa**, relacionada ao acervo do PARCEIRO.", "",
             "Por isso **não se tentou provar redundância desta pasta procurando "
             "hash idêntico em outras pastas do Programa** — essa não é a pergunta. "
             "A pergunta é: **quanto custa manter esta cópia dentro do "
             "SharePoint Programa?**", "",
             "## Custo de armazenamento desta cópia", "", "| | |", "|---|---:|",
             f"| **Bytes exatos** | **{num(p6_tot)}** |",
             f"| GB (decimal) | **{gbd(p6_tot)}** |",
             f"| GiB (binário) | **{gib(p6_tot)}** |",
             f"| Arquivos | {num(p6_arq)} |",
             f"| Subpastas | {num(p6_sub)} |",
             f"| % dos arquivos vivos do SharePoint | **{pct(p6_tot, V)}** |",
             f"| % da quota total consumida | **{pct(p6_tot, Q)}** |", "",
             "## Arquivos atuais x histórico de versões", "",
             "| Componente | Espaço | Observação |", "|---|---:|---|",
             f"| Arquivos atuais (versão corrente) | **{gib(p6_tot)}** | medido |",
             f"| Histórico de versões associado | {gib(p6_ver[0])} | "
             f"medido em {num(p6_ver[1])} arquivos da pasta |",
             f"| **Total atribuível à pasta** | **{gib(p6_tot + p6_ver[0])}** | |", "",
             f"O histórico de versões desta pasta é pequeno ({gib(p6_ver[0])}), "
             "coerente com um acervo copiado uma vez e não editado.", "",
             "## TOP 20 maiores subpastas", "",
             "| Subpasta | Arquivos | Tamanho | % da pasta |", "|---|---:|---:|---:|"]
        for n2, qtd, bts in p6_sub_top:
            L.append(f"| `{n2}` | {num(qtd)} | {gib(bts)} | {pct(bts, p6_tot)} |")
        L += ["", "## TOP 20 maiores arquivos", "",
              "| Arquivo | Tamanho |", "|---|---:|"]
        for cam, bts, iid in p6_arq_top:
            curto = cam if len(cam) < 84 else "…" + cam[-81:]
            L.append(f"| `{curto}` | {gib(bts)} |")
        L += ["", "## Tipos de arquivo predominantes", "",
              "| Extensão | Arquivos | Espaço | % da pasta |", "|---|---:|---:|---:|"]
        for ext, qtd, bts in p6_ext:
            L.append(f"| `{ext}` | {num(qtd)} | {gib(bts)} | {pct(bts, p6_tot)} |")
        L += ["", "---", "",
              "## CENÁRIO — RETIRADA DA CÓPIA DO ACERVO PARCEIRO", "",
              "> **Hipótese para decisão da gestão. Não é recomendação de "
              "exclusão e não foi executada.**", "",
              "| | |", "|---|---:|",
              f"| **Situação atual** | **{gbd(p6_tot)}** / {gib(p6_tot)} armazenados |",
              f"| **% da quota** | **{pct(p6_tot, Q)}** |",
              f"| **Potencial condicionado** | **{gbd(p6_tot)}** / {gib(p6_tot)} |",
              f"| Dos quais são cópias repetidas *dentro da própria pasta* | "
              f"{gib(p6_exc)} |",
              f"| **Potencial INCREMENTAL** (não contado em nenhum outro cenário) | "
              f"**{gib(d)}** |", "",
              "> Os " + gib(p6_exc) + " de repetição interna **não foram contados "
              "em nenhum cenário anterior** — os cenários A, B e C não incluem "
              "esta pasta. Por isso o potencial incremental é o total da pasta.", "",
              "### Condições que precisariam ser confirmadas", "",
              "1. Os arquivos originais estão **preservados na fonte oficial do "
              "PARCEIRO**",
              "2. Esta cópia no SharePoint Programa **não é necessária** para "
              "obrigação contratual, documental ou de auditoria",
              "3. **Não existe necessidade de retenção** desta cópia no Programa", "",
              "### Como o SharePoint se comporta numa eventual exclusão", "",
              "**A liberação não é instantânea.** Ao excluir, os itens vão para a "
              "**lixeira**, onde continuam **contando na quota** durante o prazo "
              "de retenção (tipicamente 93 dias, a confirmar). O espaço só é "
              "efetivamente devolvido após o esvaziamento da lixeira de primeiro "
              "e de segundo estágio.", "",
              "Portanto, uma eventual retirada desta pasta produziria:", "",
              f"- **imediato:** aumento temporário da lixeira em até {gib(p6_tot)}",
              f"- **efetivo:** liberação de {gib(p6_tot)} somente após a limpeza "
              "da lixeira",
              "", "**VALIDAÇÃO NECESSÁRIA:** `PARCEIRO`, `gestão do contrato`, "
              "`responsável documental` e `jurídico`, este último somente se "
              "houver obrigação de guarda envolvida.", "",
              "**OPORTUNIDADE CONDICIONADA À VALIDAÇÃO DA GESTÃO/PARCEIRO**", "",
              "**EXECUTADO? NÃO.**", ""]
        (saida / "ANALISE_PASTA6_ARQUIVOS_PARCEIRO.md").write_text(
            "\n".join(L), encoding="utf-8")

        # ---------------- JSON ----------------------------------------
        dados = {
            "timestamp": agora.isoformat(), "quota": Q, "vivos": V, "lixeira": LIX,
            "arquivos": NARQ, "ver": VER, "nao_id": NAO_ID,
            "cenarios": cen,
            "p5": {"total": p5_tot, "arquivos": p5_arq, "subpastas": p5_sub,
                   "fora": fora5, "so_dentro": so5, "exclusivo": sem_grupo5,
                   "exc_total": p5_exc_total, "exc_jur": p5_exc_jur,
                   "exc_p5": p5_exc_p5},
            "p6": {"total": p6_tot, "arquivos": p6_arq, "subpastas": p6_sub,
                   "exc": p6_exc, "versoes": p6_ver[0], "incremental": d},
            "superado": {"total": s_tot, "livre": s_livre, "em_dup_int": s_int,
                         "em_dup_jur": s_jur, "em_p5": s_p5, "em_p6": s_p6},
        }
        Path(cfg["banco"]).parent.joinpath("cenarios.json").write_text(
            json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")

        print("=" * 70)
        print(f"PASTAS 5 e 6 + CENARIOS — posicao {quando}")
        print("=" * 70)
        print(f"  PASTA 5  total {gib(p5_tot)} · existe fora {gib(fora5)} "
              f"({pct(fora5,p5_tot)}) · exclusivo {gib(sem_grupo5)}")
        print(f"           excedentes {gib(p5_exc_total)} = juridico {gib(p5_exc_jur)}"
              f" + PARCEIRO {gib(p5_exc_p5)}")
        print(f"  PASTA 6  total {gib(p6_tot)} ({pct(p6_tot,Q)} da quota) · "
              f"versoes {gib(p6_ver[0])} · incremental {gib(d)}")
        print()
        print("  CENARIOS INCREMENTAIS (sem dupla contagem):")
        acum = 0
        for c_ in cen:
            if "JUR" in c_["rotulo"]:
                print(f"    {'-'*62}")
                print(f"    {c_['rotulo']:<52} {gib(c_['bytes']):>12}")
                continue
            acum += c_["bytes"]
            print(f"    {c_['rotulo']:<52} {gib(c_['bytes']):>12}  "
                  f"(acum {gib(acum)})")
        print()
        print(f"  superado: total {gib(s_tot)} · incremental real {gib(s_livre)}")
        for p in ("ANALISE_PASTA5_COMPARTILHADO_PARCEIRO.md",
                  "ANALISE_PASTA6_ARQUIVOS_PARCEIRO.md", "MATRIZ_INTERSECOES.md"):
            print(f"  {saida/p}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())

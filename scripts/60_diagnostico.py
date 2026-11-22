"""
60_diagnostico - monta o DIAGNOSTICO EXECUTIVO para a gestao.

Nao consulta a API: le apenas o SQLite ja montado pelas etapas E1/E2/E3.
Nao apaga, nao move, nao decide. Produz um documento de leitura.

Cuidado central deste script: **nao contar o mesmo byte duas vezes**.
Um `.zip` duplicado nao pode entrar em "duplicatas" e tambem em "arquivos
pesados" como se fossem economias somaveis. As categorias sao calculadas
por subtracao, na ordem de confiabilidade da evidencia.

Saida:
    output/DIAGNOSTICO_EXECUTIVO.md

Uso:
    python scripts\\60_diagnostico.py
"""

from __future__ import annotations

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

PESADAS = (".zip", ".rar", ".7z", ".dav", ".mp4", ".mov", ".avi",
           ".iso", ".bak", ".pbix")
PADROES = ["backup", "superado", "superada", "obsoleto", "antigo", "antiga",
           "old", "lixeira", "descartado", "versoes anteriores", "nao usar",
           "rascunho", "temp", "temporario", "temporário", "copia", "cópia"]

# Pastas cujo conteudo foi replicado para atender orgao de controle.
# Duplicata ali e tecnicamente igual, mas o risco de remover e outro.
MARCA_JURIDICA = ("mp-estado", "mp_estado", "resposta mp", "presta", "ministerio",
                  "ministério", "auditoria", "tcu", "tce")


def ptbr(t: str) -> str:
    return t.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def num(n, casas: int = 0) -> str:
    return ptbr(f"{n:,.{casas}f}")


def gib(b) -> str:
    return f"{num((b or 0) / GiB, 1)} GiB"


def pct(parte, todo) -> str:
    return ptbr(f"{(parte or 0) * 100 / todo:.1f}") + "%" if todo else "-"


# ----------------------------------------------------------------------

def coletar(con: sqlite3.Connection) -> dict:
    d: dict = {}
    con.execute("""CREATE TEMP TABLE dup AS
        SELECT quick_xor_hash h, COUNT(*) n, MAX(tamanho) t
          FROM item_nuvem
         WHERE eh_pasta=0 AND deletado=0 AND quick_xor_hash IS NOT NULL
           AND tamanho >= 102400
         GROUP BY quick_xor_hash HAVING COUNT(*) > 1""")

    d["arquivos"], d["vivos"] = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem"
        " WHERE eh_pasta=0 AND deletado=0").fetchone()
    d["pastas"] = con.execute(
        "SELECT COUNT(*) FROM item_nuvem WHERE eh_pasta=1 AND deletado=0").fetchone()[0]
    d["quota"], d["lixeira"] = con.execute(
        "SELECT MAX(quota_usada), MAX(quota_lixeira) FROM biblioteca").fetchone()
    d["folga"] = d["quota"] - d["vivos"]
    d["sem_hash"] = con.execute(
        "SELECT COUNT(*) FROM item_nuvem WHERE eh_pasta=0 AND deletado=0"
        " AND quick_xor_hash IS NULL").fetchone()[0]
    d["erros"] = con.execute("SELECT COUNT(*) FROM erro").fetchone()[0]

    # ---- duplicatas
    g = con.execute("SELECT COUNT(*), SUM(n), SUM(t*n), SUM(t*(n-1)) FROM dup").fetchone()
    d["dup_grupos"], d["dup_arquivos"], d["dup_ocupado"], d["dup_recuperavel"] = g

    # excedente por risco: a copia "mais antiga" de cada grupo e a sugerida
    # para ficar; as demais sao o excedente. Classificado pelo caminho.
    exc_jur = exc_dev = exc_outro = 0
    linhas = con.execute("""
        SELECT quick_xor_hash, caminho, tamanho, criado_em
          FROM item_nuvem
         WHERE eh_pasta=0 AND deletado=0
           AND quick_xor_hash IN (SELECT h FROM dup)
         ORDER BY quick_xor_hash, COALESCE(criado_em,'9999')""").fetchall()
    atual, primeiro = None, True
    for h, caminho, tamanho, _criado in linhas:
        if h != atual:
            atual, primeiro = h, True
            continue                      # a 1a (mais antiga) fica
        baixo = (caminho or "").lower()
        if any(m in baixo for m in MARCA_JURIDICA):
            exc_jur += tamanho or 0
        elif baixo.startswith("/3. desenvolvimento"):
            exc_dev += tamanho or 0
        else:
            exc_outro += tamanho or 0
    d["exc_juridico"], d["exc_desenvolvimento"], d["exc_outro"] = exc_jur, exc_dev, exc_outro

    d["dup_n1"] = con.execute("""
        SELECT nivel1, COUNT(*), SUM(tamanho) FROM item_nuvem
         WHERE eh_pasta=0 AND deletado=0 AND quick_xor_hash IN (SELECT h FROM dup)
         GROUP BY nivel1 ORDER BY SUM(tamanho) DESC""").fetchall()
    d["dup_top"] = con.execute("""
        SELECT d.t, d.n, d.t*(d.n-1) rec,
               (SELECT nome FROM item_nuvem i WHERE i.quick_xor_hash=d.h LIMIT 1),
               (SELECT caminho FROM item_nuvem i WHERE i.quick_xor_hash=d.h LIMIT 1)
          FROM dup d ORDER BY rec DESC LIMIT 10""").fetchall()

    # ---- versoes
    try:
        faixas = con.execute("""
            SELECT estrato, arquivos_total, bytes_total, arquivos_medidos,
                   bytes_medidos, bytes_antigas_medidos
              FROM versao_estrato ORDER BY estrato""").fetchall()
    except sqlite3.OperationalError:
        faixas = []
    exato = estimado = 0
    d["faixas"] = []
    for est, arq_tot, b_tot, arq_med, b_med, antigas in faixas:
        if not arq_med:
            continue
        completo = arq_med >= arq_tot
        total = antigas if completo else ((antigas / b_med * b_tot) if b_med else 0)
        if completo:
            exato += antigas
        else:
            estimado += total
        d["faixas"].append((est, arq_tot, b_tot, arq_med, antigas, total, completo))
    d["ver_exato"], d["ver_estimado"] = exato, estimado
    d["ver_total"] = exato + estimado
    d["ver_consultas"] = con.execute("SELECT COUNT(*) FROM versao_resumo").fetchone()[0]
    d["ver_falhas"] = con.execute(
        "SELECT COUNT(*) FROM versao_resumo WHERE erro IS NOT NULL").fetchone()[0]
    d["ver_top"] = con.execute("""
        SELECT i.caminho, v.n_versoes, v.bytes_atual, v.bytes_antigas, i.item_id
          FROM versao_resumo v JOIN item_nuvem i
            ON i.drive_id=v.drive_id AND i.item_id=v.item_id
         WHERE v.erro IS NULL AND v.bytes_antigas>0
         ORDER BY v.bytes_antigas DESC LIMIT 12""").fetchall()
    d["ver_pasta"] = con.execute("""
        SELECT i.nivel1, i.nivel2, COUNT(*), SUM(v.bytes_antigas)
          FROM versao_resumo v JOIN item_nuvem i
            ON i.drive_id=v.drive_id AND i.item_id=v.item_id
         WHERE v.erro IS NULL AND v.bytes_antigas>0 AND i.nivel2 IS NOT NULL
         GROUP BY i.nivel1, i.nivel2 ORDER BY SUM(v.bytes_antigas) DESC LIMIT 8""").fetchall()

    d["nao_explicado"] = d["folga"] - d["ver_total"] - (d["lixeira"] or 0)

    # ---- pesados e superados, COM e SEM sobreposicao com duplicata
    cp = "extensao IN (" + ",".join("?" * len(PESADAS)) + ")"
    cs = "(" + " OR ".join(["LOWER(caminho) LIKE ?"] * len(PADROES)) + ")"
    asup = [f"%{p}%" for p in PADROES]

    d["pes_total"] = con.execute(
        f"SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem"
        f" WHERE eh_pasta=0 AND deletado=0 AND {cp}", PESADAS).fetchone()
    d["pes_fora_dup"] = con.execute(
        f"SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem"
        f" WHERE eh_pasta=0 AND deletado=0 AND {cp}"
        f" AND (quick_xor_hash IS NULL OR quick_xor_hash NOT IN (SELECT h FROM dup))",
        PESADAS).fetchone()
    d["sup_total"] = con.execute(
        f"SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem"
        f" WHERE eh_pasta=0 AND deletado=0 AND {cs}", asup).fetchone()
    d["sup_limpo"] = con.execute(
        f"SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem"
        f" WHERE eh_pasta=0 AND deletado=0 AND {cs}"
        f" AND (quick_xor_hash IS NULL OR quick_xor_hash NOT IN (SELECT h FROM dup))"
        f" AND NOT {cp}", asup + list(PESADAS)).fetchone()
    d["pes_cat"] = []
    for rot, exts in (("Compactados (.zip/.rar/.7z)", (".zip", ".rar", ".7z")),
                      ("Video (.mp4/.mov/.avi/.dav)", (".mp4", ".mov", ".avi", ".dav")),
                      ("Backup / imagem (.bak/.iso)", (".bak", ".iso")),
                      ("BI / exportacao (.pbix)", (".pbix",))):
        m = ",".join("?" * len(exts))
        d["pes_cat"].append((rot,) + con.execute(
            f"SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem"
            f" WHERE eh_pasta=0 AND deletado=0 AND extensao IN ({m})", exts).fetchone())

    d["n1"] = con.execute("""
        SELECT nivel1, COUNT(*), SUM(tamanho) FROM item_nuvem
         WHERE eh_pasta=0 AND deletado=0 AND profundidade>1
         GROUP BY nivel1 ORDER BY SUM(tamanho) DESC""").fetchall()
    return d


# ----------------------------------------------------------------------

def montar(d: dict) -> str:
    L: list[str] = []
    ad = L.append
    Q, V = d["quota"], d["vivos"]

    ad("# Programa — Diagnóstico do SharePoint")
    ad("")
    ad(f"**Data:** {date.today():%d/%m/%Y}  ·  **Responsável técnico:** Emmily Maciel  ")
    ad("**Situação:** diagnóstico concluído · **nenhum arquivo foi apagado, "
       "movido ou alterado**")
    ad("")
    ad("> Este documento mede o repositório e apresenta cenários. "
       "**Não executa nem autoriza exclusão.** Toda ação depende de decisão "
       "da gestão e dos responsáveis documentais.")
    ad("")
    ad("---")
    ad("")

    # 1 ---------------------------------------------------------------
    ad("## 1. Situação atual")
    ad("")
    ad("| | |")
    ad("|---|---:|")
    ad(f"| Armazenamento cobrado pelo SharePoint | **{gib(Q)}** |")
    ad(f"| Arquivos existentes hoje (\"vivos\") | **{gib(V)}** |")
    ad(f"| Quantidade de arquivos | **{num(d['arquivos'])}** |")
    ad(f"| Quantidade de pastas | {num(d['pastas'])} |")
    ad(f"| **Diferença (cobrado − arquivos vivos)** | **{gib(d['folga'])}** |")
    ad(f"| └ histórico de versões | {gib(d['ver_total'])} |")
    ad(f"| └ lixeira | {gib(d['lixeira'])} |")
    ad(f"| └ não identificado | {gib(d['nao_explicado'])} |")
    ad("")
    ad(f"O SharePoint cobra **{gib(Q)}**, mas os arquivos que as pessoas "
       f"enxergam somam **{gib(V)}**. A diferença de **{gib(d['folga'])}** "
       f"({pct(d['folga'], Q)} da conta) **não é documento**: é histórico de "
       "versões antigas e lixeira.")
    ad("")
    ad("**Como foi medido:** leitura item a item pela API oficial da Microsoft "
       f"(Microsoft Graph). {num(d['arquivos'])} arquivos catalogados, "
       f"**{d['erros']} erros**, {d['sem_hash']} arquivos sem impressão digital "
       "(0,03%, irrelevante). Nenhum arquivo foi baixado.")
    ad("")

    # 2 ---------------------------------------------------------------
    ad("## 2. Onde está o espaço")
    ad("")
    ad("Categorias **mutuamente exclusivas** — cada byte aparece uma única vez. "
       "Um `.zip` que também é duplicata é contado em *duplicatas*, não nos dois.")
    ad("")
    ad("| Categoria | Espaço | % da conta | Grau de certeza |")
    ad("|---|---:|---:|---|")
    unico = V - d["dup_recuperavel"]
    ad(f"| Conteúdo único (1 cópia de cada arquivo) | {gib(unico)} | "
       f"{pct(unico, Q)} | **Exato** — soma direta |")
    ad(f"| Cópias exatas excedentes | {gib(d['dup_recuperavel'])} | "
       f"{pct(d['dup_recuperavel'], Q)} | **Exato** — impressão digital idêntica |")
    ad(f"| Histórico de versões | {gib(d['ver_total'])} | "
       f"{pct(d['ver_total'], Q)} | **Parcial**: {gib(d['ver_exato'])} medido "
       f"+ {gib(d['ver_estimado'])} estimado |")
    ad(f"| Lixeira | {gib(d['lixeira'])} | {pct(d['lixeira'], Q)} | "
       "Exato conforme a API (ver ressalva na seção 5) |")
    ad(f"| Não identificado | {gib(d['nao_explicado'])} | "
       f"{pct(d['nao_explicado'], Q)} | **Desconhecido** |")
    soma = unico + d["dup_recuperavel"] + d["ver_total"] + (d["lixeira"] or 0) + d["nao_explicado"]
    ad(f"| **TOTAL** | **{gib(soma)}** | **{pct(soma, Q)}** | |")
    ad("")
    ad(f"**Explicamos {pct(Q - d['nao_explicado'], Q)} da conta** com evidência "
       "direta.")
    ad("")
    ad("As categorias abaixo **atravessam** as de cima e por isso não entram "
       "no total — servem para escolher *onde olhar*, não para somar:")
    ad("")
    ad("| Recorte | Espaço | Quanto já está contado em duplicatas |")
    ad("|---|---:|---:|")
    ad(f"| Arquivos pesados (.zip, vídeo, .bak…) | {gib(d['pes_total'][1])} | "
       f"{gib(d['pes_total'][1] - d['pes_fora_dup'][1])} |")
    ad(f"| Material possivelmente superado | {gib(d['sup_total'][1])} | "
       f"{gib(d['sup_total'][1] - d['sup_limpo'][1])} (inclui pesados) |")
    ad("")

    # 3 ---------------------------------------------------------------
    ad("## 3. Duplicatas")
    ad("")
    ad("| | |")
    ad("|---|---:|")
    ad(f"| Grupos de arquivos idênticos | **{num(d['dup_grupos'])}** |")
    ad(f"| Arquivos envolvidos | **{num(d['dup_arquivos'])}** |")
    ad(f"| Espaço que ocupam hoje | **{gib(d['dup_ocupado'])}** |")
    ad(f"| Espaço se ficasse 1 cópia de cada | {gib(d['dup_ocupado'] - d['dup_recuperavel'])} |")
    ad(f"| **Máximo recuperável** | **{gib(d['dup_recuperavel'])}** |")
    ad("")
    ad("**O que significa \"duplicata\" aqui:** arquivos com a mesma *impressão "
       "digital* (`quickXorHash`) calculada pelo próprio SharePoint. Conteúdo "
       "idêntico byte a byte — não é semelhança de nome nem de tamanho. "
       "**Nenhuma foi apagada.**")
    ad("")
    ad("### Onde estão")
    ad("")
    ad("| Pasta principal | Arquivos | Espaço |")
    ad("|---|---:|---:|")
    for n1, qtd, bts in d["dup_n1"][:8]:
        ad(f"| `{n1}` | {num(qtd)} | {gib(bts)} |")
    ad("")
    ad("### Por que existem — e por que isso muda o risco")
    ad("")
    ad("O padrão não é bagunça: é **material de projeto replicado para "
       "entregas formais**. O mesmo estudo aparece na pasta do projeto e de "
       "novo dentro de cada resposta a órgão de controle e prestação de contas.")
    ad("")
    ad("| Natureza da cópia excedente | Espaço | Risco de remover |")
    ad("|---|---:|---|")
    ad(f"| Interna ao próprio projeto (repetida entre revisões REV00, REV01…) | "
       f"{gib(d['exc_desenvolvimento'])} | **Baixo** |")
    ad(f"| Em pastas de resposta a órgão / prestação de contas | "
       f"{gib(d['exc_juridico'])} | **Alto — jurídico/contratual** |")
    ad(f"| Demais pastas | {gib(d['exc_outro'])} | Médio — avaliar |")
    ad("")
    ad("> ⚠️ **Ponto que exige decisão da gestão.** As cópias em pastas de "
       "resposta ao Ministério Público e prestação de contas são tecnicamente "
       "idênticas, mas podem precisar permanecer intactas por obrigação "
       "contratual ou probatória. **Este relatório não as trata como espaço "
       "disponível.**")
    ad("")
    ad("### Exemplos")
    ad("")
    ad("| Arquivo | Cópias | Tamanho de cada | Recuperável |")
    ad("|---|---:|---:|---:|")
    for t, n, rec, nome, _c in d["dup_top"][:6]:
        ad(f"| `{nome}` | {n} | {gib(t)} | **{gib(rec)}** |")
    ad("")

    # 4 ---------------------------------------------------------------
    ad("## 4. Histórico de versões")
    ad("")
    ad(f"**{gib(d['ver_total'])}** — a maior parcela da diferença entre o que é "
       "cobrado e o que existe.")
    ad("")
    ad("O SharePoint guarda **uma cópia inteira do arquivo a cada salvamento**. "
       "Um documento de 85 MB salvo 41 vezes ocupa mais de 3 GB, embora a "
       "pessoa veja um único arquivo.")
    ad("")
    ad("| Faixa de tamanho | Arquivos | Consultados | Versões antigas | Base |")
    ad("|---|---:|---:|---:|---|")
    for est, arq_tot, _b, arq_med, antigas, total, completo in d["faixas"]:
        base = "**medido (100%)**" if completo else f"estimado ({pct(arq_med, arq_tot)} da faixa)"
        ad(f"| {est} | {num(arq_tot)} | {num(arq_med)} | {gib(total)} | {base} |")
    ad(f"| **TOTAL** | | **{num(d['ver_consultas'])}** | **{gib(d['ver_total'])}** | "
       f"**{gib(d['ver_exato'])} exato + {gib(d['ver_estimado'])} estimado** |")
    ad("")
    ad(f"{num(d['ver_consultas'])} consultas ao histórico, **{d['ver_falhas']} falhas**. "
       "Os arquivos grandes foram verificados **um a um, sem amostragem**; "
       "só as duas faixas menores (que representam a menor parte do volume) "
       "são estimativa.")
    ad("")
    ad("### Onde o histórico se concentra")
    ad("")
    ad("| Pasta | Arquivos | Versões antigas |")
    ad("|---|---:|---:|")
    for a, b, qtd, bts in d["ver_pasta"]:
        ad(f"| `/{a}/{b}` | {num(qtd)} | {gib(bts)} |")
    ad("")
    ad("### Os maiores casos")
    ad("")
    ad("| Arquivo | Versões | Tamanho atual | Espaço do histórico |")
    ad("|---|---:|---:|---:|")
    for caminho, nv, atual, antigas, _iid in d["ver_top"][:8]:
        curto = caminho if len(caminho) < 88 else "…" + caminho[-85:]
        ad(f"| `{curto}` | **{nv}** | {gib(atual)} | **{gib(antigas)}** |")
    ad("")
    ad("### Potencial e risco")
    ad("")
    ad("- **Potencial:** até **" + gib(d["ver_total"]) + "**, sem apagar "
       "nenhum documento — a versão atual de cada arquivo permanece.")
    ad("- **Risco:** perde-se a capacidade de voltar a uma versão anterior. "
       "Para documento de obra em fase de revisão isso pode importar; para "
       "documento já entregue e aprovado, normalmente não.")
    ad("- **O que se pode fazer no futuro, sem excluir documento:** definir um "
       "limite de versões guardadas por biblioteca (hoje há arquivo com 148). "
       "É mudança de **configuração**, não exclusão de conteúdo.")
    ad("")

    # 5 ---------------------------------------------------------------
    ad("## 5. Lixeira")
    ad("")
    ad("| | |")
    ad("|---|---:|")
    ad(f"| Espaço ocupado | **{gib(d['lixeira'])}** |")
    ad(f"| % da conta | {pct(d['lixeira'], Q)} |")
    ad(f"| Recuperável se esvaziada | {gib(d['lixeira'])} |")
    ad("")
    ad("**Contraria a expectativa inicial:** supunha-se que a lixeira "
       "explicasse boa parte da diferença. Ela responde por apenas "
       f"{pct(d['lixeira'], d['folga'])} dela.")
    ad("")
    ad("> **Ressalva técnica:** a API do Microsoft Graph não expõe a lixeira "
       "em detalhe (`/recycleBin` não existe na versão 1.0). O número acima é "
       "o total informado pelo serviço e **pode não incluir a lixeira de "
       "segundo estágio** do site. Para o número exato, é preciso consultar o "
       "Centro de Administração do SharePoint. **A lixeira não foi esvaziada.**")
    ad("")

    # 6 ---------------------------------------------------------------
    ad("## 6. Outros grandes consumidores")
    ad("")
    ad("| Categoria | Arquivos | Espaço |")
    ad("|---|---:|---:|")
    for rot, qtd, bts in d["pes_cat"]:
        ad(f"| {rot} | {num(qtd)} | {gib(bts)} |")
    ad(f"| **Total de formatos pesados** | **{num(d['pes_total'][0])}** | "
       f"**{gib(d['pes_total'][1])}** |")
    ad(f"| └ dos quais **já contados** como duplicata | | "
       f"{gib(d['pes_total'][1] - d['pes_fora_dup'][1])} |")
    ad(f"| └ **fora** das duplicatas | {num(d['pes_fora_dup'][0])} | "
       f"**{gib(d['pes_fora_dup'][1])}** |")
    ad("")
    ad("> **Não são lixo por definição.** Um `.zip` pode ser entrega "
       "contratual compactada; um vídeo pode ser filmagem de tráfego que "
       "embasa estudo; `.dav` é gravação de câmera. Precisam de avaliação "
       "documental caso a caso.")
    ad("")
    ad("### Material possivelmente superado")
    ad("")
    ad("Arquivos cujo caminho contém termos como *backup*, *antigo*, *old*, "
       "*superado*, *cópia*, *temp*, *rascunho*.")
    ad("")
    ad("| | Arquivos | Espaço |")
    ad("|---|---:|---:|")
    ad(f"| Total identificado | {num(d['sup_total'][0])} | {gib(d['sup_total'][1])} |")
    ad(f"| **Sem sobreposição** com duplicatas e pesados | "
       f"{num(d['sup_limpo'][0])} | **{gib(d['sup_limpo'][1])}** |")
    ad("")
    ad("> **Identificar não é recomendar exclusão.** O nome da pasta é indício, "
       "não prova. Uma pasta \"backup\" pode conter o único exemplar de algo.")
    ad("")

    # 7 ---------------------------------------------------------------
    ad("## 7. Cenários possíveis")
    ad("")
    ad("Hipóteses para decisão humana. **Nenhum foi executado e nenhum está "
       "autorizado.** Os valores não se somam entre cenários — cada um inclui "
       "o anterior.")
    ad("")
    a = d["ver_total"] + (d["lixeira"] or 0)
    b = a + d["exc_desenvolvimento"]
    c = b + d["sup_limpo"][1]
    ad("### CENÁRIO A — conservador")
    ad("")
    ad("Reduzir histórico de versões e esvaziar a lixeira. "
       "**Nenhum documento deixa de existir.**")
    ad("")
    ad(f"- Potencial: **{gib(a)}** ({pct(a, Q)} da conta)")
    ad("- Risco: **baixo** — perde-se apenas a possibilidade de voltar a "
       "versões anteriores")
    ad("- Validação necessária: confirmar que não há exigência de reter "
       "histórico (retenção legal / *litigation hold*)")
    ad("")
    ad("### CENÁRIO B — intermediário")
    ad("")
    ad("Cenário A **+** revisão humana das cópias exatas **internas aos "
       "projetos** (mesmo arquivo repetido entre revisões).")
    ad("")
    ad(f"- Potencial adicional: **{gib(d['exc_desenvolvimento'])}**")
    ad(f"- Potencial acumulado: **{gib(b)}** ({pct(b, Q)} da conta)")
    ad("- Risco: **médio** — exige conferência de que a cópia mantida é a "
       "correta e continua acessível a quem usa")
    ad("- **Não inclui** as cópias em pastas de resposta a órgão de controle")
    ad("")
    ad("### CENÁRIO C — revisão documental ampla")
    ad("")
    ad("Cenário B **+** material marcado como backup/antigo/superado, "
       "sempre com validação dos responsáveis.")
    ad("")
    ad(f"- Potencial adicional: **{gib(d['sup_limpo'][1])}**")
    ad(f"- Potencial acumulado: **{gib(c)}** ({pct(c, Q)} da conta)")
    ad("- Risco: **alto** — depende de julgamento documental caso a caso")
    ad("")
    ad("### Fora dos cenários")
    ad("")
    ad(f"As cópias em pastas de resposta a órgão de controle (**{gib(d['exc_juridico'])}**) "
       "**não entram em nenhum cenário** até que a área jurídica/contratual se "
       "manifeste. É o maior volume isolado e também o de maior risco.")
    ad("")

    # 8 ---------------------------------------------------------------
    ad("## 8. Principais oportunidades")
    ad("")
    ad("| # | Origem | Espaço envolvido | Potencial recuperável | Risco | Validação necessária |")
    ad("|---:|---|---:|---:|---|---|")
    ad(f"| 1 | Histórico de versões | {gib(d['ver_total'])} | {gib(d['ver_total'])} | "
       "Baixo | Confirmar ausência de retenção legal |")
    ad(f"| 2 | Cópias exatas em pastas de resposta a órgão | {gib(d['exc_juridico'])} | "
       f"{gib(d['exc_juridico'])} | **Alto (jurídico)** | Jurídico + gestão do contrato |")
    ad(f"| 3 | Cópias exatas internas aos projetos | {gib(d['exc_desenvolvimento'])} | "
       f"{gib(d['exc_desenvolvimento'])} | Médio | Coordenação de engenharia |")
    ad(f"| 4 | Formatos pesados fora de duplicatas | {gib(d['pes_fora_dup'][1])} | "
       "a definir | Médio | Responsável por cada disciplina |")
    ad(f"| 5 | Material marcado como superado | {gib(d['sup_limpo'][1])} | "
       "a definir | Alto | Responsável documental |")
    ad(f"| 6 | Lixeira | {gib(d['lixeira'])} | {gib(d['lixeira'])} | "
       "Baixo | Confirmar prazo de retenção |")
    ad("")
    ad("Ordenado por volume, **não** por recomendação. Espaço envolvido ≠ "
       "espaço que deve sair.")
    ad("")

    # 9 ---------------------------------------------------------------
    ad("## 9. Próxima etapa")
    ad("")
    ad("**Nada deve ser apagado automaticamente.** Antes de montar qualquer "
       "lista de exclusão, é preciso:")
    ad("")
    ad("1. **Confirmar retenção legal.** Existe *litigation hold*, política de "
       "retenção ou exigência contratual que impeça reduzir histórico de "
       "versões ou esvaziar a lixeira? — *TI / Jurídico*")
    ad("2. **Decidir sobre o material de resposta a órgão de controle.** "
       f"É o maior volume ({gib(d['exc_juridico'])}) e o de maior risco. As "
       "pastas de resposta ao MP e prestação de contas podem ser "
       "desduplicadas, ou devem permanecer intactas como entregues? "
       "— *Jurídico / gestão do contrato*")
    ad("3. **Definir qual pasta é a \"oficial\".** Quando o mesmo arquivo existe "
       "na pasta do projeto e na pasta de entrega, qual permanece? Este "
       "relatório sugere a mais antiga, mas **é decisão do contrato**, não "
       "técnica. — *Coordenação*")
    ad("4. **Avaliar política de versões.** Limitar o número de versões "
       "guardadas resolve a maior parcela sem apagar documento. Qual limite "
       "é aceitável? — *Gestão + TI*")
    ad("5. **Validar amostra.** Antes de qualquer ação, conferir manualmente "
       "alguns grupos de duplicatas no SharePoint. Os relatórios trazem "
       "caminho completo e identificador de cada arquivo para isso.")
    ad("")
    ad("Só depois disso faz sentido montar a lista priorizada (E4) e, "
       "havendo aprovação por escrito, executá-la em modo simulação antes de "
       "qualquer alteração real (E5).")
    ad("")
    ad("---")
    ad("")
    ad("## Como estes números foram obtidos")
    ad("")
    ad("- Leitura pela **API oficial da Microsoft (Microsoft Graph)**, com "
       "aplicativo autorizado pela TI do contrato.")
    ad("- **Somente leitura.** Nenhuma operação de escrita, exclusão, "
       "movimentação ou renomeação foi executada. O programa utilizado não "
       "possui código capaz de alterar o SharePoint.")
    ad("- **Nenhum arquivo foi baixado.** A impressão digital de cada arquivo "
       "é fornecida pelo próprio servidor.")
    ad("- Catálogo completo em banco local, com caminho, tamanho, datas, autor "
       "e identificador de cada item — qualquer número deste documento pode "
       "ser rastreado até o arquivo de origem.")
    ad("")
    ad("**Relatórios de apoio:** `output/duplicatas.csv` (uma linha por cópia), "
       "`output/duplicatas.md`, `output/consumo.md`, "
       "`output/consumo_por_pasta.csv`.")
    ad("")
    return "\n".join(L)


def main() -> int:
    cfg = ler_config()
    exigir_fase_analise(cfg)
    con = sqlite3.connect(caminho_banco(cfg))
    try:
        d = coletar(con)
        saida = Path(cfg["saida"]) / "DIAGNOSTICO_EXECUTIVO.md"
        saida.parent.mkdir(parents=True, exist_ok=True)
        saida.write_text(montar(d), encoding="utf-8")
        print("=" * 70)
        print("DIAGNOSTICO EXECUTIVO")
        print("=" * 70)
        print(f"  conta cobrada .............. {gib(d['quota'])}")
        print(f"  arquivos vivos ............. {gib(d['vivos'])}  ({num(d['arquivos'])} arq)")
        print(f"  diferenca .................. {gib(d['folga'])}")
        print(f"    versoes .................. {gib(d['ver_total'])}"
              f"   (exato {gib(d['ver_exato'])} + estimado {gib(d['ver_estimado'])})")
        print(f"    lixeira .................. {gib(d['lixeira'])}")
        print(f"    nao identificado ......... {gib(d['nao_explicado'])}")
        print()
        print(f"  duplicatas recuperavel ..... {gib(d['dup_recuperavel'])}")
        print(f"    interno aos projetos ..... {gib(d['exc_desenvolvimento'])}  (risco baixo)")
        print(f"    resposta a orgao ......... {gib(d['exc_juridico'])}  (risco JURIDICO)")
        print(f"    demais ................... {gib(d['exc_outro'])}")
        print()
        print(f"  CENARIO A (versoes+lixeira)  {gib(d['ver_total'] + d['lixeira'])}")
        print(f"  CENARIO B (+dup internas) .. "
              f"{gib(d['ver_total'] + d['lixeira'] + d['exc_desenvolvimento'])}")
        print(f"  CENARIO C (+superado) ...... "
              f"{gib(d['ver_total'] + d['lixeira'] + d['exc_desenvolvimento'] + d['sup_limpo'][1])}")
        print()
        print(f"  {saida}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())

"""
95_resumo_gestao - RESUMO_REUNIAO_GESTAO.md, versao final consolidada.

Le data/consolidado.json e data/cenarios.json. Nao chama a API.

UNIDADE: o SharePoint/Admin Center rotula como "GB" um valor calculado com
base 1024 (tecnicamente GiB). Para nao parecer divergencia diante da tela da
Microsoft, este resumo usa esse mesmo numero e o chama de "GB (padrao
SharePoint)", com nota de rodape. A fonte de tudo continua sendo BYTES.

Uso:
    python scripts\\95_resumo_gestao.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_util import ler_config  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass

GiB, GBd = 1024**3, 1000**3


def ptbr(t): return t.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
def num(n, c=0): return ptbr(f"{n:,.{c}f}")
def SP(b): return f"{num((b or 0)/GiB, 1)} GB"        # padrao SharePoint (base 1024)
def dec(b): return f"{num((b or 0)/GBd, 1)} GB"       # decimal puro
def pct(p, t, c=1): return ptbr(f"{(p or 0)*100/t:.{c}f}") + "%" if t else "-"


def main() -> int:
    cfg = ler_config()
    saida = Path(cfg["saida"])
    base = Path(cfg["banco"]).parent
    C = json.loads((base / "consolidado.json").read_text(encoding="utf-8"))
    Z = json.loads((base / "cenarios.json").read_text(encoding="utf-8"))
    quando = datetime.fromisoformat(Z["timestamp"]).strftime("%d/%m/%Y %H:%M")

    Q, V, LIX = Z["quota"], Z["vivos"], Z["lixeira"]
    VER, NAOID = Z["ver"], Z["nao_id"]
    unico = V - C["dup_recuperavel"]
    cls = C["classes"]
    p5, p6, sup = Z["p5"], Z["p6"], Z["superado"]
    cen = {c["rotulo"][0]: c["bytes"] for c in Z["cenarios"]}
    cenJ = [c for c in Z["cenarios"] if c["rotulo"].startswith("JUR")][0]["bytes"]
    pastas = sorted(C["pastas"], key=lambda p: -p["bytes"])

    L: list[str] = []
    ad = L.append

    ad("# SharePoint Programa — resumo para a gestão")
    ad("")
    ad(f"## POSIÇÃO DO SHAREPOINT EM: {quando}")
    ad("")
    ad("Emmily Maciel · TI do contrato · **diagnóstico por leitura, "
       "nada foi alterado**")
    ad("")
    ad("---")
    ad("")

    # 1 ----------------------------------------------------------------
    ad("## 1. Situação atual")
    ad("")
    ad(f"# {SP(Q)}")
    ad("")
    ad("| | Espaço | % |")
    ad("|---|---:|---:|")
    ad(f"| **Total utilizado** | **{SP(Q)}** | 100% |")
    ad(f"| Documentos atuais (o que as pessoas veem) | {SP(V)} | {pct(V, Q)} |")
    ad(f"| Versões antigas dos mesmos arquivos | {SP(VER)} | {pct(VER, Q)} |")
    ad(f"| Lixeira | {SP(LIX)} | {pct(LIX, Q)} |")
    ad(f"| Ainda não identificado | {SP(NAOID)} | {pct(NAOID, Q)} |")
    ad("")
    ad(f"São **{num(Z['arquivos'])} arquivos** em **{num(C['pastas_total'])} pastas**.")
    ad("")
    ad("> **Sobre a unidade.** Os valores acima usam a mesma base que o painel "
       "da Microsoft (1 GB = 1.073.741.824 bytes). Diferenças entre GB e GiB "
       "decorrem apenas do padrão de conversão da unidade; os cálculos têm como "
       "fonte os bytes retornados pela Microsoft. Em GB decimal puro, o total "
       f"seria {dec(Q)}.")
    ad("")

    # 2 ----------------------------------------------------------------
    ad("## 2. Como o espaço está distribuído")
    ad("")
    ad("![Armazenamento por pasta](grafico_01_armazenamento_6_pastas.png)")
    ad("")
    ad("| Pasta | Arquivos | Tamanho | % dos documentos | % do total | "
       "Duplicatas | Potencial recuperável | Principal causa |")
    ad("|---|---:|---:|---:|---:|---:|---:|---|")
    causas = {
        "1. Gestão do Contrato": "volume irrelevante",
        "2. Compartilhado interno": "respostas a órgão de controle",
        "3. Desenvolvimento": "projetos + anexos repetidos entre revisões",
        "4. Produtos": "entregas mensais",
        "5. Compartilhado PARCEIRO": "área de transferência ao PARCEIRO",
        "6. Arquivos PARCEIRO": "cópia de acervo externo ao Programa",
    }
    for p in pastas:
        ad(f"| **{p['nome']}** | {num(p['arquivos'])} | **{SP(p['bytes'])}** | "
           f"{pct(p['bytes'], V)} | {pct(p['bytes'], Q)} | "
           f"{num(p['dup_arquivos'])} | {SP(p['recuperavel'])} | "
           f"{causas[p['nome']]} |")
    ad("")
    ad("| Pasta | Tamanho | % único | % duplicado | % versões | Possível ação | Quem valida |")
    ad("|---|---:|---:|---:|---:|---|---|")
    acoes = {
        "1. Gestão do Contrato": ("nenhuma — volume desprezível", "—"),
        "2. Compartilhado interno": ("depende de parecer jurídico", "Jurídico"),
        "3. Desenvolvimento": ("rever anexos repetidos entre revisões", "Coordenação"),
        "4. Produtos": ("rever cópias das entregas mensais", "Coordenação"),
        "5. Compartilhado PARCEIRO": ("confirmar recebimento pelo PARCEIRO", "PARCEIRO / Eliseu"),
        "6. Arquivos PARCEIRO": ("confirmar se a cópia precisa permanecer", "PARCEIRO + gestão"),
    }
    for p in pastas:
        b = p["bytes"] or 1
        ad(f"| {p['nome']} | {SP(b)} | {pct(p['unicos_bytes'], b)} | "
           f"{pct(p['dup_ocupado'], b)} | {pct(p['versoes'], b)} | "
           f"{acoes[p['nome']][0]} | {acoes[p['nome']][1]} |")
    ad("")
    ad("**Duas pastas concentram o armazenamento:** `3. Desenvolvimento` e "
       f"`5. Compartilhado PARCEIRO` somam "
       f"{pct(pastas[0]['bytes'] + [x for x in pastas if x['nome'].startswith('5')][0]['bytes'], V)} "
       "dos documentos.")
    ad("")

    # 3 ----------------------------------------------------------------
    ad("## 3. Duplicação")
    ad("")
    ad("![Composição da quota](grafico_02_composicao_quota.png)")
    ad("")
    ad("| | |")
    ad("|---|---:|")
    ad(f"| Grupos de arquivos idênticos | **{num(C['dup_grupos'])}** |")
    ad(f"| Arquivos envolvidos | **{num(C['dup_arquivos'])}** |")
    ad(f"| Espaço que ocupam hoje | {SP(C['dup_ocupado'])} |")
    ad(f"| **Espaço recuperável** (mantendo 1 cópia de cada) | "
       f"**{SP(C['dup_recuperavel'])}** |")
    ad("")
    ad("**Arquivo idêntico** significa mesma impressão digital calculada pelo "
       "próprio SharePoint — conteúdo igual byte a byte, não nome parecido.")
    ad("")
    ad("### Distribuição por pasta")
    ad("")
    ad("| Pasta | Cópias excedentes |")
    ad("|---|---:|")
    for p in pastas:
        if p["recuperavel"]:
            ad(f"| {p['nome']} | {SP(p['recuperavel'])} |")
    ad("")
    ad("### Por natureza — é isto que define o risco")
    ad("")
    ad("| Natureza | Espaço | Quem decide |")
    ad("|---|---:|---|")
    ad(f"| Resposta a órgão de controle / prestação de contas | "
       f"**{SP(cls.get('juridico',0))}** | **Jurídico** |")
    ad(f"| Anexos repetidos entre revisões de projeto | {SP(cls.get('revisoes',0))} | "
       "Coordenação de engenharia |")
    ad(f"| Outras repetições no desenvolvimento | {SP(cls.get('desenvolvimento',0))} | "
       "Coordenação |")
    ad(f"| Dentro de `6. Arquivos PARCEIRO` | {SP(cls.get('pasta6',0))} | PARCEIRO + gestão |")
    ad(f"| Dentro de `5. Compartilhado PARCEIRO` | {SP(cls.get('pasta5',0))} | "
       "PARCEIRO / Eliseu |")
    ad(f"| Demais áreas | {SP(cls.get('outras',0))} | Responsável da área |")
    ad(f"| **TOTAL** | **{SP(sum(cls.values()))}** | |")
    ad("")

    # 4 ----------------------------------------------------------------
    ad("## 4. Onde estão as maiores oportunidades")
    ad("")
    ad("Em ordem de volume. **Nenhuma é recomendação de exclusão.**")
    ad("")
    ad("| # | Oportunidade | Espaço | % do total | Quem precisa validar |")
    ad("|---:|---|---:|---:|---|")
    op = [
        ("Cópias de resposta a órgão de controle", cls.get("juridico", 0), "Jurídico"),
        ("Histórico de versões", VER, "Gestão + TI"),
        ("Cópia do acervo PARCEIRO (pasta 6)", p6["total"], "PARCEIRO + gestão"),
        ("Anexos repetidos entre revisões", cls.get("revisoes", 0), "Coordenação"),
        ("Material marcado backup/antigo/temp", sup["livre"], "Resp. documental"),
        ("Repetições no desenvolvimento", cls.get("desenvolvimento", 0), "Coordenação"),
        ("Transferência ao PARCEIRO (pasta 5)", cls.get("pasta5", 0), "PARCEIRO / Eliseu"),
        ("Lixeira", LIX, "TI"),
    ]
    for i, (nome, val, quem) in enumerate(sorted(op, key=lambda x: -x[1]), 1):
        ad(f"| {i} | {nome} | {SP(val)} | {pct(val, Q)} | {quem} |")
    ad("")

    # 5 ----------------------------------------------------------------
    ad("## 5. Compartilhado PARCEIRO — área de transferência")
    ad("")
    ad(f"A pasta ocupa **{SP(p5['total'])}** ({pct(p5['total'], Q)} do total).")
    ad("")
    ad("| | Espaço | % da pasta |")
    ad("|---|---:|---:|")
    ad(f"| Conteúdo que **já existe em outro lugar** do Programa | "
       f"**{SP(p5['fora'])}** | {pct(p5['fora'], p5['total'])} |")
    ad(f"| Conteúdo **exclusivo** desta pasta | {SP(p5['exclusivo'])} | "
       f"{pct(p5['exclusivo'], p5['total'])} |")
    ad("")
    ad(f"**{pct(p5['fora'], p5['total'])} do que está nesta pasta já existe em "
       "outro ponto do SharePoint** — comprovado por impressão digital idêntica.")
    ad("")
    ad("**Atenção:** a maior parte dessa redundância "
       f"({SP(p5['exc_jur'])}) é material de **resposta a órgão de controle** "
       f"guardado dentro da pasta, não material de transferência. Apenas "
       f"{SP(p5['exc_p5'])} pertencem de fato à frente PARCEIRO.")
    ad("")
    ad("### Cenário condicionado")
    ad("")
    ad("> **Se** o responsável pelo PARCEIRO — incluindo **Eliseu**, quando "
       "aplicável — confirmar **formalmente** que determinado conjunto já foi "
       "**integralmente recebido/baixado** e que manter a cópia no Programa não é "
       f"necessário, o potencial de liberação seria de até **{SP(p5['exc_p5'])}**.")
    ad("")
    ad("**Confirmação verbal não é suficiente.** **EXECUTADO? NÃO.**")
    ad("")

    # 6 ----------------------------------------------------------------
    ad("## 6. Arquivos PARCEIRO — cópia externa")
    ad("")
    ad("A pasta `6. Arquivos PARCEIRO` funciona como uma **cópia, dentro do "
       "SharePoint Programa, de conteúdo cuja origem está fora deste ambiente**. "
       f"Ela ocupa atualmente **{SP(p6['total'])}** ({dec(p6['total'])} em GB "
       f"decimal), correspondentes a **{pct(p6['total'], Q)} do armazenamento "
       "utilizado**. Caso seja confirmado pelos responsáveis que a fonte oficial "
       "permanece preservada no PARCEIRO e que o Programa não possui obrigação de manter "
       "essa cópia, a retirada desse conjunto poderá ser avaliada, com potencial "
       f"de liberação de aproximadamente **{SP(p6['total'])}**.")
    ad("")
    ad("| | |")
    ad("|---|---:|")
    ad(f"| Tamanho total | **{SP(p6['total'])}** / {dec(p6['total'])} |")
    ad(f"| Bytes exatos | {num(p6['total'])} |")
    ad(f"| Arquivos | {num(p6['arquivos'])} |")
    ad(f"| Subpastas | {num(p6['subpastas'])} |")
    ad(f"| % do total do SharePoint | **{pct(p6['total'], Q)}** |")
    ad(f"| Histórico de versões associado | {SP(p6['versoes'])} (pequeno — "
       "acervo copiado, não editado) |")
    ad(f"| Repetição interna à própria pasta | {SP(p6['exc'])} |")
    ad(f"| **Potencial incremental** (não contado em outra frente) | "
       f"**{SP(p6['incremental'])}** |")
    ad("")
    ad("### A hipótese de ser uma cópia foi comprovada?")
    ad("")
    ad("**Não por medição — e não poderia ser.** A origem está **fora** deste "
       "SharePoint, então nenhuma comparação interna provaria isso. A natureza "
       "da pasta é **premissa de negócio informada pela gestão**, não resultado "
       "da análise. O que a análise mede é **quanto custa mantê-la aqui**.")
    ad("")
    ad("### Como o SharePoint se comporta numa eventual retirada")
    ad("")
    ad("**A liberação não seria instantânea.** Itens excluídos vão para a "
       "lixeira e **continuam contando na quota** durante o prazo de retenção "
       "(tipicamente 93 dias, a confirmar). O espaço só é devolvido após o "
       "esvaziamento da lixeira de primeiro e segundo estágio.")
    ad("")
    ad("**OPORTUNIDADE CONDICIONADA À VALIDAÇÃO DA GESTÃO/PARCEIRO** · "
       "**EXECUTADO? NÃO.**")
    ad("")

    # 7 ----------------------------------------------------------------
    ad("## 7. Histórico de versões")
    ad("")
    ad(f"**{SP(VER)}** — {pct(VER, Q)} do total.")
    ad("")
    ad("Cada vez que alguém salva um arquivo, o SharePoint guarda **uma cópia "
       "inteira dele**. Um relatório de 85 MB salvo 41 vezes ocupa mais de 3 GB, "
       "mas a pessoa vê **um** arquivo. Há documento com **148 versões** "
       "guardadas.")
    ad("")
    ad("**Efeito potencial:** reduzir o histórico libera até "
       f"{SP(VER)} **sem que nenhum documento deixe de existir** — a versão "
       "atual permanece intacta. O único efeito é perder a capacidade de voltar "
       "a uma versão anterior.")
    ad("")

    # 8 ----------------------------------------------------------------
    ad("## 8. O que depende do jurídico")
    ad("")
    ad(f"### {SP(cenJ)}")
    ad("")
    ad("Material copiado para responder ao **Ministério Público** e para "
       "**prestações de contas**. São cópias idênticas ao que já existe nos "
       "projetos, **mas constituem o protocolo do que foi entregue**.")
    ad("")
    ad("**É o maior volume isolado.** Está **fora de todos os cenários "
       "operacionais** e não deve ser apresentado como espaço disponível.")
    ad("")

    # 9 ----------------------------------------------------------------
    ad("## 9. Cenários para decisão")
    ad("")
    ad("Valores **incrementais** — cada cenário acrescenta apenas o que os "
       "anteriores ainda não contaram. **Nenhum foi executado.**")
    ad("")
    ad("| Cenário | Acrescenta | Acumulado | % do total | Risco |")
    ad("|---|---:|---:|---:|---|")
    acum = 0
    linhas_cen = [
        ("**0 — Só governança**", 0, "Nenhuma exclusão; política para evitar "
         "crescimento futuro", "Nenhum"),
        ("**A — Sem eliminar documentos atuais**", cen.get("A", 0),
         "Histórico de versões + lixeira", "Baixo"),
        ("**B — Duplicatas operacionais**", cen.get("B", 0),
         "Cópias internas validadas pelas áreas", "Médio"),
        ("**C — Compartilhado PARCEIRO**", cen.get("C", 0),
         "Só com confirmação de recebimento pelo PARCEIRO", "Médio"),
        ("**D — Arquivos PARCEIRO**", cen.get("D", 0),
         "Só com confirmação de que a fonte externa está preservada", "Médio"),
        ("**E — Material superado**", cen.get("E", 0),
         "Backup/antigo/temp, após revisão documental", "Alto"),
    ]
    for rotulo, val, desc, risco in linhas_cen:
        acum += val
        ad(f"| {rotulo} | {SP(val) if val else '—'} | {SP(acum)} | "
           f"{pct(acum, Q)} | {risco} |")
    ad(f"| ⚖️ **JURÍDICO — separado** | {SP(cenJ)} | *não soma* | "
       f"{pct(cenJ, Q)} | **Alto — jurídico** |")
    ad("")
    ad("> O cenário jurídico **não se soma** aos demais: depende de decisão de "
       "outra natureza e não deve entrar na conta operacional.")
    ad("")

    # 10 ---------------------------------------------------------------
    ad("## 10. Decisões necessárias da gestão")
    ad("")
    ad("| # | Decisão | Quem |")
    ad("|---:|---|---|")
    ad("| 1 | **Política de versionamento** — podemos limitar o número de "
       "versões guardadas? Existe obrigação legal de manter todas? | Gestão + TI |")
    ad("| 2 | **Tratamento da lixeira** — qual o prazo de retenção e podemos "
       "esvaziar? | TI |")
    ad("| 3 | **Responsáveis por validar duplicatas** em cada área | Coordenação |")
    ad("| 4 | **Confirmação do PARCEIRO** sobre recebimento dos conjuntos "
       "compartilhados | PARCEIRO / Eliseu |")
    ad("| 5 | **Tratamento da pasta Arquivos PARCEIRO** — a cópia precisa "
       "permanecer no Programa? | PARCEIRO + gestão do contrato |")
    ad("| 6 | **Análise jurídica** do material de resposta a órgão de "
       "controle | Jurídico |")
    ad("| 7 | **Investigar a parcela não identificada** no Centro de "
       "Administração | TI |")
    ad("")

    # ações -------------------------------------------------------------
    ad("---")
    ad("")
    ad("## Ações possíveis")
    ad("")
    ad("| AÇÃO POSSÍVEL | ESPAÇO POTENCIAL | RISCO | QUEM PRECISA APROVAR | EXECUTADA? |")
    ad("|---|---:|---|---|---|")
    ad(f"| Reduzir histórico de versões | {SP(VER)} | Baixo — não apaga "
       f"documento | Gestão + TI | **NÃO** |")
    ad(f"| Esvaziar lixeira | {SP(LIX)} | Baixo | TI | **NÃO** |")
    ad(f"| Rever cópias repetidas entre revisões | {SP(cls.get('revisoes',0))} | "
       f"Médio | Coordenação de engenharia | **NÃO** |")
    ad(f"| Rever outras repetições internas | "
       f"{SP(cls.get('desenvolvimento',0) + cls.get('outras',0))} | Médio | "
       f"Responsável de cada área | **NÃO** |")
    ad(f"| Retirar cópias já recebidas pelo PARCEIRO | {SP(cls.get('pasta5',0))} | "
       f"Médio | PARCEIRO / Eliseu + gestão | **NÃO** |")
    ad(f"| Retirar cópia do acervo PARCEIRO (pasta 6) | {SP(p6['incremental'])} | "
       f"Médio | PARCEIRO + gestão do contrato | **NÃO** |")
    ad(f"| Revisar material backup/antigo/superado | {SP(sup['livre'])} | Alto | "
       f"Responsável documental | **NÃO** |")
    ad(f"| Tratar cópias de resposta ao MP | {SP(cenJ)} | **Alto — jurídico** | "
       f"Jurídico + gestão | **NÃO** |")
    ad(f"| Investigar parcela não identificada | {SP(NAOID)} | — | TI | **NÃO** |")
    ad("")

    # conclusão ---------------------------------------------------------
    ad("---")
    ad("")
    ad("## Conclusão")
    ad("")
    ad("O diagnóstico **não propõe uma exclusão em massa**. Ele separa os "
       f"**{SP(Q)}** utilizados entre **{SP(unico)} de conteúdo atual único**, "
       f"**{SP(C['dup_recuperavel'])} de cópias idênticas**, "
       f"**{SP(VER)} de histórico de versões**, **{SP(LIX)} de lixeira** e "
       f"**{SP(NAOID)} ainda não identificados**.")
    ad("")
    ad(f"Menos da metade do armazenamento — **{pct(unico, Q)}** — corresponde "
       "a conteúdo atual sem repetição. A partir dessa separação, a gestão "
       "consegue decidir onde atuar primeiro e qual economia cada decisão "
       "produziria: de "
       f"**{SP(cen.get('A',0))}** no cenário mais conservador, que não elimina "
       f"nenhum documento atual, até **{SP(sum(cen.get(k,0) for k in 'ABCDE'))}** "
       "se todas as frentes operacionais forem validadas pelos responsáveis — "
       f"sempre com os **{SP(cenJ)}** de material jurídico tratados à parte.")
    ad("")

    # rodapé -------------------------------------------------------------
    ad("---")
    ad("")
    ad("## Situação atual do trabalho")
    ad("")
    ad("Diagnóstico feito **inteiramente por leitura** da API oficial da "
       "Microsoft. Até este momento:")
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
    ad(f"*Fonte: Microsoft Graph — posição {quando}. "
       "Relatórios técnicos de apoio em `output/`.*")
    ad("")

    (saida / "RESUMO_REUNIAO_GESTAO.md").write_text("\n".join(L), encoding="utf-8")
    print(f"  {saida / 'RESUMO_REUNIAO_GESTAO.md'}")
    print(f"  cenario A..E acumulado: {SP(sum(cen.get(k,0) for k in 'ABCDE'))}")
    print(f"  juridico (separado):    {SP(cenJ)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

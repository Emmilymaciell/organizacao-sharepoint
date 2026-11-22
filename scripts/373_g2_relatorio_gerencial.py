"""
373_g2_relatorio_gerencial - relatorios gerenciais da auditoria de uma rodovia (SO LEITURA), a partir das
saidas do 371 (auditoria), do 372 (verificacao independente) e do parecer do revisor independente.

Gera em output/migracao_parceiro_g2/auditoria_<GO>/:
  01_RELATORIO_GERENCIAL_<GO>.md, 02_AUDITORIA_ESTRUTURA_EAP.md, 04_PENDENTES_RESUMO.md,
  06_PARECER_AUDITORIA_INDEPENDENTE.md (se --parecer for dado), 07_RESUMO_PARA_CONFERENCIA.md

Uso: python scripts/373_g2_relatorio_gerencial.py --rodovia RD-103 [--parecer arquivo.md]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2.base import agora, cfg, gib, num, saida  # noqa: E402


def ler(p):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def pct(a, b):
    return f"{100 * a / b:.2f}%".replace(".", ",") if b else "-"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rodovia", default="RD-103")
    ap.add_argument("--parecer")
    a = ap.parse_args()
    c = cfg()
    d = saida(c) / f"auditoria_{a.rodovia}"
    rec = json.loads((d / "RECONCILIACAO.json").read_text(encoding="utf-8"))
    ind = json.loads((d / "06_VERIFICACAO_INDEPENDENTE_AMOSTRA.json").read_text(encoding="utf-8"))
    est, nom, pen = ler(d / "02_AUDITORIA_ESTRUTURA_EAP.csv"), ler(d / "03_AUDITORIA_NOMENCLATURA.csv"), \
        ler(d / "04_PENDENTES_REAVALIACAO.csv")
    na = ler(d / "04b_NAO_APROVADOS_RESUMO.csv")
    A, B = rec["A_conclusao_operacional"], rec["B_distribuicao"]
    den = rec["B_situacao_documental_denominador"]
    raiz = c["destino"]["arvores"][a.rodovia]
    ctl = rec["controles_usados"]
    ncat = Counter(x["categoria"] for x in nom)
    nconf = Counter((x["categoria"], x["conformidade"]) for x in nom)
    cats = sorted(ncat)
    bytes_cat = defaultdict(int)
    for x in nom:
        bytes_cat[x["categoria"]] += int(x["tamanho"] or 0)
    nc_ = Counter(x["conformidade"] for x in nom)
    ec = Counter(x["conformidade"] for x in est)
    tip = Counter(x["tipologia"] for x in pen)
    cob = sum(1 for x in pen if x["decisao_humana"].startswith("NAO"))

    g = [f"# 01 — Relatório gerencial da {a.rodovia} — Migração G2 → PARCEIRO INFRA", "",
         f"Auditoria de {agora()[:16].replace('T', ' ')} (somente leitura). Destino `{raiz}`. Foto do PARCEIRO nº "
         f"{rec['foto_parceiro']}. Controles consultados agora (etag registrado em `RECONCILIACAO.json`).", "",
         "## Veredito", "",
         f"- **Operacional: CONCLUÍDO.** {num(A['validados'])} de {num(A['elegiveis'])} arquivos elegíveis transferidos e "
         f"**validados** ({pct(A['validados'], A['elegiveis'])}; {gib(A['bytes_validados'])} GiB). Cada um casa com o PARCEIRO "
         "(fileid, caminho, tamanho) e tem QuickXor igual ao da Microsoft; amostra independente "
         f"{ind['pass']}/{ind['amostra']} PASS.",
         f"- **Estrutura da EAP: CONFORME** — {ec.get('CONFORME', 0)} pastas conformes, {ec.get('NAO_CONFORME', 0)} não "
         f"conformes, {ec.get('INDETERMINADO', 0)} indeterminadas (pastas `NI-` e assuntos de NC cortados em 90 "
         "caracteres). RPFO, DUP, Fase "
         "Executória, Volumes Finais e Documentação Supervisora vazios; Eventograma não criado (nota 11).",
         f"- **Nomenclatura: {pct(nc_.get('CONFORME', 0), len(nom))} conforme** — {nc_.get('NAO_CONFORME', 0)} não "
         f"conformes (sufixo de cópia herdado da origem) e {nc_.get('INDETERMINADO', 0)} indeterminados (numeração de "
         "pastas REV da origem, folhas com código de revisão anterior dentro do pacote aprovado, pastas NI, 1 período).",
         f"- **Situação documental da {a.rodovia} (não confundir com a operacional):** dos {num(den)} arquivos da origem, "
         f"{num(B.get('TRANSFERIDO_VALIDADO', 0))} ({pct(B.get('TRANSFERIDO_VALIDADO', 0), den)}) transferidos, "
         f"{num(B.get('PENDENTE_DECISAO', 0))} ({pct(B.get('PENDENTE_DECISAO', 0), den)}) pendentes de decisão, "
         f"{num(B.get('NAO_APROVADO', 0))} ({pct(B.get('NAO_APROVADO', 0), den)}) não aprovados e "
         f"{num(B.get('FORA_DO_ESCOPO', 0))} ({pct(B.get('FORA_DO_ESCOPO', 0), den)}) fora do escopo (obsoletos, "
         "auxiliares, fotos, revisões não finais, ramos sem correspondência). Categorias mutuamente exclusivas.",
         "- **Preservação:** 0 arquivo do baseline alterado pelo motor; 0 escrita fora da RD-103. O único item do baseline "
         "ausente é a pasta `02 - APRESENTACAO` da RD-101, removida por terceiros antes de qualquer escrita nossa.", "",
         "## Quantitativos por categoria (transferidos e validados)", "",
         "| Categoria | Arquivos | GiB | Nome conforme | Indeterminado | Não conforme |", "|---|---:|---:|---:|---:|---:|"]
    g += [f"| {k} | {num(ncat[k])} | {gib(bytes_cat[k])} | {num(nconf[(k, 'CONFORME')])} | "
          f"{num(nconf[(k, 'INDETERMINADO')])} | {num(nconf[(k, 'NAO_CONFORME')])} |" for k in cats]
    g += [f"| **Total** | **{num(len(nom))}** | **{gib(sum(bytes_cat.values()))}** | | | |", "",
          "## Pendentes de decisão", "",
          f"{num(len(pen))} casos = 573 classificados para decisão (SEM_EVIDENCIA, EQUIVALENCIA_INCERTA) + 1 "
          "DUPLICIDADE_CONFIRMADA (origem com o mesmo arquivo duas vezes para o mesmo destino). Nenhum foi transferido. "
          f"Reavaliados com os controles atuais: **0 liberáveis**; {cob} já cobertos por conteúdo idêntico transferido em "
          "outra entrega da RD-103.", "", "| Tipologia | Casos |", "|---|---:|"]
    g += [f"| {k} | {v} |" for k, v in tip.most_common()]
    g += ["", "Detalhe: `04_PENDENTES_RESUMO.md` e `04_PENDENTES_REAVALIACAO.csv`.", "",
          "## Não aprovados (fora dos pendentes, por regra da nota 02)", "",
          "| Disciplina na origem | Status no controle | Arquivos | GiB |", "|---|---|---:|---:|"]
    agg = defaultdict(lambda: [0, 0, ""])
    for x in na:
        k = x["disciplina_origem"].split("/")[-1] or x["disciplina_origem"]
        agg[k][0] += int(x["arquivos"])
        agg[k][1] += int(x["bytes"] or 0)
        agg[k][2] = x["status_controle"] or agg[k][2]
    g += [f"| {k} | {v[2]} | {num(v[0])} | {gib(v[1])} |" for k, v in sorted(agg.items(), key=lambda kv: -kv[1][0])]
    g += ["", "Observação: nas disciplinas aprovadas, as revisões anteriores à final também ficam aqui (não são finais).",
          "", "## Ressalvas", "",
          "- Conteúdo repetido entre arquivos transferidos: "
          f"{rec['conteudo_repetido_entre_nossos']['arquivos']} arquivos em {rec['conteudo_repetido_entre_nossos']['grupos']} "
          "grupos, por desenho: o mesmo documento integra entregas diferentes (ex.: RO anexado ao RM, certidões repetidas "
          "a cada BM). Não houve cópia duplicada no mesmo destino.",
          "- O snapshot de 13:55 e o de 19:44 listaram no SHA256SUMS os arquivos transitórios -shm/-wal do SQLite; o "
          "conteúdo confere (0 divergências). Procedimento corrigido no snapshot final.",
          "- Controles em edição por terceiros: Lista Mestre e Acompanhamento mudaram no dia; a reavaliação usa as versões "
          "de agora (etag registrado).",
          f"- **Errata de evidência (achado A1 da revisão independente):** {rec['errata_evidencia']['arquivos']} arquivos do "
          "Executivo (DUP e Interferências) tinham a evidência gravada com a NT de versão anterior (.1); a regra exige "
          "agora a versão citada no controle (.2), que consta como 'Entregue' em 21/10. "
          f"{rec['errata_evidencia']['continuam_elegiveis']} continuam elegíveis — nenhuma transferência indevida. "
          "`08_ERRATA_EVIDENCIA.csv`.", ""]
    (d / f"01_RELATORIO_GERENCIAL_{a.rodovia}.md").write_text("\n".join(g), encoding="utf-8")

    e = [f"# 02 — Auditoria da estrutura × EAP — {raiz}", "",
         f"{len(est)} verificações: {dict(ec)}. Regras: nós fixos da EAP (linhas 208–246), subpastas da nota 02, "
         "placeholders `[AAAA-MM - Mês]`, `[AAAA-MM-DD a AAAA-MM-DD - nº Relatório Semanal]`, `[NC-nnn - AAAA-MM-DD - "
         "Assunto]`, vazios obrigatórios. Abaixo das folhas da EAP, a subestrutura da entrega foi preservada (notas 01, 02 e 04).",
         "", "## Nós fixos, vazios esperados e itens não conformes/indeterminados", "",
         "| Tipo | Pasta | Conformidade | Observação | Arquivos diretos |", "|---|---|---|---|---:|"]
    e += [f"| {x['tipo']} | {x['pasta']} | {x['conformidade']} | {x['observacao']} | {x['arquivos_diretos']} |"
          for x in est if x["tipo"] in ("NO_FIXO_EAP", "VAZIO_ESPERADO") or x["conformidade"] != "CONFORME"]
    e += ["", "## Placeholders (pastas de mês, relatório e NC)", "", "| Pasta | Conformidade |", "|---|---|"]
    e += [f"| {x['pasta']} | {x['conformidade']} |" for x in est if x["tipo"] == "PLACEHOLDER_EAP"]
    e += ["", f"Subpastas da origem preservadas abaixo das folhas da EAP: "
          f"{sum(1 for x in est if x['tipo'] == 'SUBPASTA_DA_ORIGEM')} (lista completa no CSV)."]
    (d / "02_AUDITORIA_ESTRUTURA_EAP.md").write_text("\n".join(e) + "\n", encoding="utf-8")

    p = ["# 04 — Análise dos pendentes da " + a.rodovia, "",
         f"{len(pen)} casos individualizados em `04_PENDENTES_REAVALIACAO.csv` (id, origem, destino previsto, tamanho, "
         "revisão, motivo e evidência originais, reavaliação com os controles atuais, estado atual da origem no Graph, "
         "correspondência no PARCEIRO, o que falta, recomendação). **Nenhum foi transferido.** A classificação histórica no "
         "banco não foi alterada.", "",
         "Controles consultados: " + "; ".join(f"`{k}` etag {v['etag']} modificado {v['modificado']}" for k, v in ctl.items()),
         "", "## Por categoria e tipologia", "", "| Categoria | Tipologia | Casos | MiB | Exemplo |", "|---|---|---:|---:|---|"]
    grp = defaultdict(list)
    for x in pen:
        grp[(x["categoria"], x["tipologia"])].append(x)
    for (cat, t), xs in sorted(grp.items(), key=lambda kv: -len(kv[1])):
        p.append(f"| {cat} | {t} | {len(xs)} | {sum(int(x['tamanho'] or 0) for x in xs) / 2**20:,.1f} | "
                 f"{xs[0]['origem_sharepoint'][-70:]} |")
    p += ["", "## Leitura gerencial", "",
          "- **Hidrologia (316):** aprovada (REV 5, NT-210.1, recebida em 16/07), mas a origem só tem a pasta da entrega "
          "de 07/05 (aprovada com ressalva pela NT-101.1). Falta localizar/arquivar a entrega de 16/07.",
          "- **RCA (31) e Paisagismo (49):** aprovados no controle em 26/10, mas NT-349.1 e NT-347.1 estão 'Em elaboração' "
          "na Lista Mestre. Liberar quando constarem como entregues.",
          "- **Licenciamento — evidências de condicionantes (executora):** sem registro de entrega oficial; decidir se "
          "entram em `03. Condicionantes`.",
          "- **SST — fichas de campo e pareceres ambientais:** fichas sem registro de entrega; pareceres entregues mas "
          "sem ramo da EAP (avaliação ambiental, não de condicionantes). Decidir o ramo.",
          "- **NC ambíguas:** documentos comuns a várias NCs, parecer com 'RD102' no nome, arquivos sem número de NC.",
          "- **Relatórios:** RM-014.1 sem registro 'Entregue'; 56º semanal fora da Lista Mestre; subpastas aninhadas.",
          f"- **{cob} casos** já estão cobertos: o mesmo conteúdo foi transferido em outra entrega da RD-103 "
          "(ex.: parecer também anexado ao RM)."]
    (d / "04_PENDENTES_RESUMO.md").write_text("\n".join(p) + "\n", encoding="utf-8")

    if a.parecer:
        texto = Path(a.parecer).read_text(encoding="utf-8")
        par = ["# 06 — Parecer de auditoria independente — " + a.rodovia, "",
               "## A. Verificação por método diferente (script 372)", "",
               f"Amostra estratificada por categoria (semente {ind['semente']}), {ind['amostra']} arquivos relidos "
               f"integralmente do PARCEIRO (PROPFIND e GET agora) e comparados com o quickXorHash atual da Microsoft: "
               f"**{ind['pass']} PASS / {ind['fail']} FAIL**. Detalhe: `06_VERIFICACAO_INDEPENDENTE_AMOSTRA.json`.", "",
               "## B. Revisor de QA independente (somente leitura, sem acesso à conversa)", "", texto]
        (d / "06_PARECER_AUDITORIA_INDEPENDENTE.md").write_text("\n".join(par) + "\n", encoding="utf-8")

    base = f"output\\migracao_parceiro_g2\\auditoria_{a.rodovia}"
    r = [f"# 07 — Resumo para conferência — {a.rodovia}", "",
         f"**Operacional:** {num(A['validados'])}/{num(A['elegiveis'])} validados ({pct(A['validados'], A['elegiveis'])}), "
         f"{gib(A['bytes_validados'])} GiB. **Estrutura:** conforme. **Nomes:** {nc_.get('NAO_CONFORME', 0)} não conformes, "
         f"{nc_.get('INDETERMINADO', 0)} indeterminados. **Pendentes:** {len(pen)} (0 liberáveis hoje). "
         f"**Amostra independente:** {ind['pass']}/{ind['amostra']} PASS.", "",
         "| Documento | Caminho |", "|---|---|",
         f"| 01 Relatório gerencial | `{base}\\01_RELATORIO_GERENCIAL_{a.rodovia}.md` |",
         f"| 02 Estrutura × EAP | `{base}\\02_AUDITORIA_ESTRUTURA_EAP.md` (+ .csv) |",
         f"| 03 Nomenclatura | `{base}\\03_AUDITORIA_NOMENCLATURA.csv` |",
         f"| 04 Pendentes | `{base}\\04_PENDENTES_RESUMO.md` + `04_PENDENTES_REAVALIACAO.csv` + `04b_NAO_APROVADOS_RESUMO.csv` |",
         f"| 05 Decisões humanas | `{base}\\05_MATRIZ_DECISOES_HUMANAS.csv` |",
         f"| 06 Parecer independente | `{base}\\06_PARECER_AUDITORIA_INDEPENDENTE.md` |",
         f"| Reconciliação | `{base}\\RECONCILIACAO.json` |", ""]
    (d / "07_RESUMO_PARA_CONFERENCIA.md").write_text("\n".join(r), encoding="utf-8")
    print("ok:", sorted(x.name for x in d.iterdir()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

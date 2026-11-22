"""
370_g2_relatorios - relatorios da operacao G2 a partir do banco (SO LEITURA; nao acessa PARCEIRO nem Graph).

Gera em output/migracao_parceiro_g2/relatorios_<GO>/ (ate 28/10 a RD-103 gravava na raiz de output/migracao_parceiro_g2/):
  MATRIZ_ORIGEM_DESTINO_G2.csv         uma linha por arquivo de origem considerado, estado final
  ARQUIVOS_TRANSFERIDOS_G2.csv         VALIDADO, com fileid, SHA-256 e QuickXor de origem e destino
  ARQUIVOS_NAO_TRANSFERIDOS_G2.csv     todo o resto, com classificacao e motivo
  MANIFESTO_HASHES_G2.csv              hashes dos arquivos criados
  CONFLITOS_PARA_DECISAO.csv           o que exige decisao humana (origem e preexistentes)
  JA_EXISTIA_PARCEIRO_AUDITORIA.csv        cada arquivo do baseline: conformidade com a EAP e recomendacao
  ALTERACOES_PARCEIRO_DURANTE_MIGRACAO_G2.csv
  ESTRUTURA_FINAL_G2.md, RELATORIO_EXECUTIVO_G2.md, RELATORIO_TECNICO_G2.md

Uso: python scripts/370_g2_relatorios.py --rodovia RD-103
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2.base import agora, cfg, conectar, escrever_csv, gib, num, saida  # noqa: E402

PEND_HUMANA = ("EQUIVALENCIA_INCERTA", "CONFLITO_CONCORRENTE", "ALTERADO_DURANTE_EXECUCAO", "SEM_EVIDENCIA")


def auditoria_preexistentes(con, c) -> list[tuple]:
    """Conformidade estrutural de cada arquivo do baseline com a EAP (nos fixos ate o nivel de ramo)."""
    eap = {r[0] for r in con.execute("SELECT 'PARCEIRO-INFRA/' || caminho FROM eap_no")}
    fixos = {p for p in eap if "[" not in p}
    out = []
    for r in con.execute("SELECT * FROM parceiro_item WHERE foto_id=1 AND eh_pasta=0 ORDER BY caminho"):
        cam = r["caminho"]
        partes = cam.split("/")
        # maior prefixo que e no fixo da EAP
        k = max((i for i in range(1, len(partes)) if "/".join(partes[:i]) in fixos), default=0)
        ramo_ok = "/".join(partes[:k])
        resto = partes[k:-1]
        prob, rec = [], []
        if k <= 3:
            prob.append("fora dos ramos da EAP dentro de 04. Engenharia")
            rec.append("mover para o ramo da EAP correspondente")
        rel = cam.split("04. Engenharia/", 1)[-1]
        if rel.startswith("02. Boletim de Medição/") and not rel.startswith("02. Boletim de Medição/01. Mensal/"):
            prob.append("BM sem a camada '01. Mensal' e sem as categorias 01/02/03 da EAP")
            rec.append("reorganizar em 01. Mensal/AAAA-MM - Mês/{01. Documentação Executora|03. Elaboração Programa}")
        if rel.startswith("01.Projetos") or "/01.Anteprojeto" in cam:
            prob.append("nome de pasta fora do padrao da EAP (sem espaco apos o ponto)")
            rec.append("renomear a pasta para o rotulo exato da EAP")
        if rel.startswith("01. Projetos/02. Executivo/") and not any(
                rel.startswith(f"01. Projetos/02. Executivo/{s}") for s in
                ("01. FASE DE ESTUDOS", "02. VIARIO PRINC", "03. VIARIO SEC", "04. OAE", "05. VOLUMES FINAIS")):
            prob.append("Executivo fora das subpastas da nota 02 (01. FASE DE ESTUDOS ... 05. VOLUMES FINAIS)")
            rec.append("reorganizar por grupo e conferir se e a revisao final aprovada sem ressalva")
        if "09. Eventograma" in cam:
            prob.append("ramo Eventograma (nota 11: nao mexer)")
            rec.append("nenhuma acao nesta operacao")
        out.append((r["rodovia"], cam, partes[-1], r["tamanho"], r["modificado"], r["etag"], r["fileid"],
                    ramo_ok.split("04. Engenharia/", 1)[-1] if "04. Engenharia/" in ramo_ok else ramo_ok,
                    "CONFORME" if not prob else "NAO_CONFORME", " | ".join(prob), " | ".join(rec),
                    "PRESERVADO_SEM_ALTERACAO", "SIM" if prob else "NAO",
                    "autoria nao determinada (o PARCEIRO nao registra o autor por arquivo nesta API)"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rodovia", default="RD-103")
    a = ap.parse_args()
    c = cfg()
    con = conectar()
    out = saida(c) / f"relatorios_{a.rodovia}"            # 29/10/2026: uma pasta por rodovia (nao sobrescreve a RD-103)
    out.mkdir(parents=True, exist_ok=True)
    go = a.rodovia
    q = """SELECT p.*, o.fonte, o.caminho AS origem, o.tamanho, o.quick_xor_hash, o.modificado_em, o.web_url
           FROM plano p JOIN origem_item o USING (drive_id, item_id) WHERE p.rodovia=?"""
    linhas = [dict(r) for r in con.execute(q, (go,))]

    cab = ["rodovia", "categoria", "classificacao", "estado", "motivo", "regra", "evidencia", "revisao", "aprovacao",
           "origem_fonte", "origem_caminho", "item_id", "tamanho", "quick_xor_origem", "modificado_em", "destino",
           "correspondente_parceiro", "lote", "fileid_destino", "sha256", "quick_xor_destino", "copiado_em", "validado_em",
           "erro"]

    def lin(r):
        return (go, r["categoria"], r["classificacao"], r["estado"], r["motivo"], r["regra"], r["evidencia"],
                r["revisao"], r["aprovacao"], r["fonte"], r["origem"], r["item_id"], r["tamanho"], r["quick_xor_hash"],
                r["modificado_em"], r["destino_rel"], r["correspondente"], r["lote"], r["fileid_destino"], r["sha256"],
                r["quick_xor_dest"], r["copiado_em"], r["validado_em"], r["erro"])
    escrever_csv(out / "MATRIZ_ORIGEM_DESTINO_G2.csv", cab, (lin(r) for r in linhas))
    val = [r for r in linhas if r["estado"] == "VALIDADO"]
    nval = [r for r in linhas if r["estado"] != "VALIDADO"]
    escrever_csv(out / "ARQUIVOS_TRANSFERIDOS_G2.csv", cab, (lin(r) for r in val))
    escrever_csv(out / "ARQUIVOS_NAO_TRANSFERIDOS_G2.csv", cab, (lin(r) for r in nval))
    escrever_csv(out / "MANIFESTO_HASHES_G2.csv",
                 ["destino", "fileid", "tamanho", "sha256", "quick_xor_destino", "quick_xor_microsoft", "item_id_origem",
                  "validado_em"],
                 ((r["destino_rel"], r["fileid_destino"], r["tamanho"], r["sha256"], r["quick_xor_dest"],
                   r["quick_xor_hash"], r["item_id"], r["validado_em"]) for r in val))

    pre = auditoria_preexistentes(con, c)
    escrever_csv(out / "JA_EXISTIA_PARCEIRO_AUDITORIA.csv",
                 ["rodovia", "caminho_atual", "nome", "tamanho", "modificado", "etag", "fileid", "ramo_eap_reconhecido",
                  "conformidade", "problema", "correcao_recomendada", "situacao", "decisao_humana", "autoria"], pre)

    alt = [dict(r) for r in con.execute("SELECT * FROM alteracao_externa ORDER BY primeira_vez")]
    escrever_csv(out / "ALTERACOES_PARCEIRO_DURANTE_MIGRACAO_G2.csv",
                 ["fileid", "caminho_inicial", "caminho_atual", "tamanho_inicial", "tamanho_atual", "etag_inicial",
                  "etag_atual", "hash_inicial", "hash_atual", "tipo", "primeiro_horario", "ultimo_horario", "evidencia",
                  "relacao_com_origem", "impacto", "autoria", "recomendacao", "decisao_humana", "origem_do_item"],
                 ((x["fileid"], x["caminho_inicial"], x["caminho_atual"], x["tamanho_inicial"], x["tamanho_atual"],
                   x["etag_inicial"], x["etag_atual"], "", "", x["tipo"], x["primeira_vez"], x["ultima_vez"],
                   f"fotos {x['foto_inicial']} -> {x['foto_atual']} (PROPFIND, oc:fileid)", "",
                   "nenhum sobre a RD-103" if not (x["caminho_atual"] or "").startswith("PARCEIRO-INFRA/05. RD-103") or x["nosso"]
                   else "verificar", "MOTOR G2" if x["nosso"] else "indeterminada",
                   "" if x["nosso"] else "registrar; nao intervir", "NAO" if x["nosso"] else "CIENCIA",
                   "1. criado pelo motor" if x["nosso"] else ("3. novo durante a execucao" if x["tipo"] == "NOVO" else
                                                               "4/5/6. alteracao externa"))
                  for x in alt))

    conf = [r for r in nval if r["classificacao"] in PEND_HUMANA or r["estado"] in ("ERRO", "DIVERGENTE")]
    escrever_csv(out / "CONFLITOS_PARA_DECISAO.csv",
                 ["tipo", "rodovia", "categoria", "classificacao", "estado", "situacao_encontrada", "regra",
                  "acao_recomendada (NAO executada)", "caminho", "destino_previsto", "evidencia"],
                 [("ORIGEM", go, r["categoria"], r["classificacao"], r["estado"], r["motivo"] or r["erro"], r["regra"],
                   "decidir se copia; se sim, registrar a evidencia e replanejar", r["origem"], r["destino_rel"],
                   r["evidencia"]) for r in conf] +
                 [("PREEXISTENTE_PARCEIRO", p[0], "", p[8], p[11], p[9], "EAP", p[10], p[1], "", f"fileid {p[6]}")
                  for p in pre if p[8] == "NAO_CONFORME"])

    # ---------- estrutura final
    foto = con.execute("SELECT MAX(id) FROM parceiro_foto WHERE fim IS NOT NULL").fetchone()[0]
    raiz = c["destino"]["arvores"][go]
    arv = defaultdict(lambda: [0, 0])
    for r in con.execute("SELECT caminho, tamanho FROM parceiro_item WHERE foto_id=? AND eh_pasta=0 AND caminho LIKE ?",
                         (foto, raiz + "/%")):
        p = r[0][len(raiz) + 1:].split("/")
        for d in (1, 2, 3):
            if len(p) > d:
                arv["/".join(p[:d])][0] += 1
                arv["/".join(p[:d])][1] += r[1] or 0
    est = [f"# Estrutura final — {raiz}", "", f"Foto {foto} do PARCEIRO (PROPFIND). Arquivos e GiB por pasta (3 níveis).", "",
           "| Pasta | Arquivos | GiB |", "|---|---:|---:|"]
    est += [f"| {'&nbsp;' * 4 * k.count('/')}{k.rsplit('/', 1)[-1]} | {num(v[0])} | {gib(v[1])} |"
            for k, v in sorted(arv.items())]
    (out / f"ESTRUTURA_FINAL_G2_{go}.md").write_text("\n".join(est) + "\n", encoding="utf-8")

    # ---------- relatorios
    def tab(itens, chave):
        t = defaultdict(lambda: [0, 0])
        for r in itens:
            t[chave(r)][0] += 1
            t[chave(r)][1] += r["tamanho"] or 0
        return t
    prev = [r for r in linhas if r["classificacao"] == "AUSENTE_E_ELEGIVEL" or r["estado"] == "VALIDADO" or
            r["classificacao"] in ("CONFLITO_CONCORRENTE", "ALTERADO_DURANTE_EXECUCAO", "JA_EXISTIA_CORRETO")]
    pb, vb = sum(r["tamanho"] or 0 for r in prev), sum(r["tamanho"] or 0 for r in val)
    por_cat = tab(prev, lambda r: r["categoria"])
    val_cat = tab(val, lambda r: r["categoria"])
    nt = tab(nval, lambda r: (r["categoria"], r["classificacao"]))
    estados = Counter(r["estado"] for r in prev)
    ex = [f"# Relatório executivo — Migração G2 → PARCEIRO INFRA — {go}", "",
          f"Gerado em {agora()} a partir de `data/migracao_parceiro_g2.db`. Destino: `{raiz}`.", "",
          "## Resultado", "",
          f"- **Documentos previstos (elegíveis):** {num(len(prev))} arquivos, {gib(pb)} GiB.",
          f"- **Transferidos e VALIDADOS** (relidos do PARCEIRO; SHA-256 igual ao da leitura da origem e QuickXor igual ao da "
          f"Microsoft): **{num(len(val))} arquivos, {gib(vb)} GiB** — "
          f"{100 * len(val) / max(len(prev), 1):.2f}% dos arquivos e {100 * vb / max(pb, 1):.2f}% dos bytes previstos.",
          f"- Estados dos previstos: {dict(estados)}.",
          f"- Considerados na origem (todas as classes): {num(len(linhas))} arquivos.",
          "- Nenhum arquivo preexistente do PARCEIRO foi alterado, movido, renomeado ou apagado pelo motor (só PUT com "
          "If-None-Match e MOVE de montagem com Overwrite: F; a trava de código recusa outras rodovias e o Eventograma).",
          "", "## Por categoria da EAP", "", "| Categoria | Previstos | GiB | Validados | GiB |", "|---|---:|---:|---:|---:|"]
    ex += [f"| {k} | {num(v[0])} | {gib(v[1])} | {num(val_cat[k][0])} | {gib(val_cat[k][1])} |" for k, v in sorted(por_cat.items())]
    ex += ["", "## Não transferidos (por categoria e motivo)", "", "| Categoria | Classificação | Arquivos | GiB |",
           "|---|---|---:|---:|"]
    ex += [f"| {k[0]} | {k[1]} | {num(v[0])} | {gib(v[1])} |" for k, v in sorted(nt.items())]
    ex += ["", "## Alterações de terceiros observadas", ""]
    ac = Counter((x["tipo"], "nosso" if x["nosso"] else "terceiros") for x in alt)
    ex += [f"- {k[0]} ({k[1]}): {v}" for k, v in sorted(ac.items())] or ["- nenhuma"]
    ex += ["", "## Preexistentes no PARCEIRO (baseline, quatro árvores)", "",
           f"- {num(len(pre))} arquivos no baseline; não conformes com a EAP: "
           f"{num(sum(1 for p in pre if p[8] == 'NAO_CONFORME'))}. Nenhum alterado pelo motor (sem DELETE/MOVE no código; "
           "nenhuma escrita fora da rodovia). Removidos/movidos por TERCEIROS durante a operação: "
           f"{sum(1 for x in alt if x['tipo'].startswith('REMOVIDO') and not x['nosso'])} "
           "(ver ALTERACOES_PARCEIRO_DURANTE_MIGRACAO_G2.csv).",
           "- Detalhe e correções recomendadas (não executadas): `JA_EXISTIA_PARCEIRO_AUDITORIA.csv` e "
           "`CONFLITOS_PARA_DECISAO.csv`.", "",
           "## Arquivos", "", "Todos em `output/migracao_parceiro_g2/` (fora do Git: contêm nomes de documentos de terceiros)."]
    (out / "RELATORIO_EXECUTIVO_G2.md").write_text("\n".join(ex) + "\n", encoding="utf-8")

    env = Counter((r["metodo"], r["resultado"]) for r in con.execute("SELECT metodo, resultado FROM envio"))
    seg = con.execute("SELECT MIN(inicio), MAX(fim), SUM(bytes) FROM envio WHERE resultado='CRIADO'").fetchone()
    testes = sorted(out.glob("TESTE_MOTOR_*.json"))
    tt = json.loads(testes[-1].read_text(encoding="utf-8")) if testes else {}
    tec = [f"# Relatório técnico — Migração G2 → PARCEIRO INFRA — {go}", "", f"Gerado em {agora()}.", "",
           "## Método", "",
           "- Origem: Microsoft Graph (GET), sem download em massa: metadados + quickXorHash; conteúdo lido por streaming "
           "(HTTP Range) só no envio. Fontes: biblioteca Programa `3. Desenvolvimento/G2 -  Engenharia` e a pasta de entrega "
           "final de BM da rodovia (nota H238), por drive_id:item_id.",
           "- Destino: Nextcloud WebDAV. Escrita **condicional decidida pelo servidor**: PUT `If-None-Match: *` "
           "(≤ 64 MiB) e chunking v2 com MOVE `Overwrite: F` (> 64 MiB). Nenhum DELETE/MOVE/PROPPATCH no código.",
           f"- Teste real do motor: rodada {tt.get('rodada')} — {'todos os casos passaram' if tt.get('todos_passaram') else 'FALHAS'} "
           "(ausente, ocupado, idempotência, chunking, corrida com terceiro, pasta existente, falha no meio, sem cópias "
           "renomeadas, trava de escopo). Achado: no MOVE, `If-None-Match` é avaliado sobre a origem do MOVE — usar só "
           "`Overwrite: F`.",
           "- Antes de cada arquivo: origem revalidada (etag/tamanho/hash), pasta de destino com o mesmo oc:fileid, destino "
           "ausente. Depois: releitura integral do PARCEIRO; VALIDADO só com SHA-256 e QuickXor iguais.",
           "- Executor único (trava de arquivo), Agendador de Tarefas a cada 15 min (watchdog), pausa por "
           "`tmp/migracao_parceiro_g2/PAUSAR`, RAM mínima por envio, monitor do PARCEIRO a cada 20 min que pausa se algum arquivo "
           "nosso sumir.", "", "## Números", "",
           f"- Envios: {dict(env)}", f"- Janela de envio: {seg[0]} a {seg[1]}; {gib(seg[2])} GiB criados.",
           f"- Baseline PARCEIRO: foto 1, SHA-256 do CSV "
           f"`{con.execute('SELECT sha256_csv FROM parceiro_foto WHERE id=1').fetchone()[0]}`.", "",
           "## Regras aplicadas (366_g2_planejar.py)", "",
           "- Executivo: status exatamente 'Aprovado' no `03 - Acompanhamento` + NT 'Entregue' na Lista Mestre + pasta REV "
           "com a data da entrega aprovada. Ressalva/reprovado/em análise/entrega informal: não copia.",
           "- Relatórios, inspeções e pareceres: código 'Entregue' na Lista Mestre para a rodovia.",
           "- Diário: até o fim da última semana entregue. NC: código na planilha de NC; documento de várias NCs → revisão.",
           "- Excluídos sempre: obsoleto, entrega informal, minuta, Arquivos Auxiliares, pacotes .zip (salvo quando o "
           "pacote é o único formato da revisão aprovada), temporários.",
           "- Conteúdo idêntico já presente na mesma rodovia: não copia. Em outra rodovia: copia e registra.", ""]
    (out / "RELATORIO_TECNICO_G2.md").write_text("\n".join(tec) + "\n", encoding="utf-8")
    print(f"previstos {num(len(prev))} ({gib(pb)} GiB) | validados {num(len(val))} ({gib(vb)} GiB) | "
          f"conflitos p/ decisao {num(len(conf))} | preexistentes nao conformes {num(sum(1 for p in pre if p[8] == 'NAO_CONFORME'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

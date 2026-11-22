"""
344_migra_relatorio_retorno - resumo para a Emmily ao voltar: execucao, saude, integridade,
prazo e pendencias. So le o banco e os logs; regenerado pelo watchdog a cada checagem.

Saidas: output/migracao_parceiro/RELATORIO_RETORNO.md e .json
Uso:    python scripts/344_migra_relatorio_retorno.py --lotes "PILOTO-02,PILOTO-03,..."
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_parceiro.base import BRT, agora, cfg, conectar, gib, num, saida  # noqa: E402

PRAZO = datetime(2026, 10, 20, 12, 0, tzinfo=BRT)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--lotes", required=True)
    a = ap.parse_args()
    lotes = [x.strip() for x in a.lotes.split(",") if x.strip()]
    c = cfg()
    con = conectar()
    marc = ",".join("?" * len(lotes))
    por_lote = {}
    for lt in lotes:
        est = {r[0]: (r[1], r[2] or 0) for r in con.execute(
            "SELECT p.estado, COUNT(*), SUM(o.tamanho) FROM plano_item p JOIN origem_item o USING(item_id) "
            "WHERE p.lote=? GROUP BY 1", (lt,))}
        tot = (sum(v[0] for v in est.values()), sum(v[1] for v in est.values()))
        por_lote[lt] = {"total": tot, "estados": est, "concluido": set(est) == {"VALIDADO"}}
    tot_arq = sum(v["total"][0] for v in por_lote.values())
    tot_b = sum(v["total"][1] for v in por_lote.values())
    val_arq = sum(v["estados"].get("VALIDADO", (0, 0))[0] for v in por_lote.values())
    val_b = sum(v["estados"].get("VALIDADO", (0, 0))[1] for v in por_lote.values())
    inicio = con.execute(f"SELECT MIN(inicio) FROM transferencia WHERE lote IN ({marc})", lotes).fetchone()[0]
    # vazao validada na ultima hora (bytes de arquivos VALIDADO por hora)
    vh = con.execute(f"SELECT COALESCE(SUM(o.tamanho),0), COUNT(*) FROM verificacao v JOIN origem_item o USING(item_id) "
                     f"JOIN plano_item p USING(item_id) WHERE v.resultado='VALIDADO' AND p.lote IN ({marc}) "
                     f"AND v.quando >= ?", (*lotes, datetime.now(BRT).replace(microsecond=0).isoformat()[:13]))
    b_hora, n_hora = vh.fetchone()
    b_1h = con.execute(f"SELECT COALESCE(SUM(o.tamanho),0), COUNT(*) FROM verificacao v JOIN origem_item o USING(item_id) "
                       f"JOIN plano_item p USING(item_id) WHERE v.resultado='VALIDADO' AND p.lote IN ({marc}) "
                       f"AND julianday(v.quando) >= julianday('now') - 1.0/24", lotes).fetchone()
    rest_arq, rest_b = tot_arq - val_arq, tot_b - val_b
    eta = None
    if b_1h[0] > 0 or b_1h[1] > 0:
        h_bytes = rest_b / b_1h[0] if b_1h[0] else 0
        h_arq = rest_arq / b_1h[1] if b_1h[1] else 0
        eta = max(h_bytes, h_arq)          # o gargalo (bytes ou quantidade) manda
    transit = con.execute(f"SELECT COUNT(*) FROM transferencia WHERE lote IN ({marc}) AND resultado='FALHOU'", lotes).fetchone()[0]
    paradas = con.execute(f"SELECT COUNT(*) FROM transferencia WHERE lote IN ({marc}) AND resultado='PARADA_GLOBAL'", lotes).fetchone()[0]
    prob = dict(con.execute(f"SELECT estado, COUNT(*) FROM plano_item WHERE lote IN ({marc}) AND estado IN "
                            f"('EXCECAO','CONFLITO','DIVERGENTE','ORIGEM_ALTERADA','ESGOTADO','FALHOU') GROUP BY 1", lotes).fetchall())
    incidentes = []
    arq_inc = RAIZ / "logs" / "incidentes_migracao.csv"
    if arq_inc.exists():
        with open(arq_inc, encoding="utf-8-sig", newline="") as f:
            incidentes = list(csv.DictReader(f, delimiter=";"))
    interv = [i for i in incidentes if i.get("acao") not in ("nenhuma", "")]
    pend = dict(con.execute("SELECT estado, COUNT(*) FROM plano_item WHERE lote IS NULL OR lote NOT IN "
                            f"({marc}) GROUP BY 1", lotes).fetchall())
    pend_lotes = [r[0] for r in con.execute("SELECT nome FROM lote WHERE aprovado_em IS NULL")]

    agora_dt = datetime.now(BRT)
    prev = None
    if eta is not None:
        from datetime import timedelta
        prev = (agora_dt + timedelta(hours=eta)).strftime("%d/%m %H:%M")
    linhas = [
        f"# Migração Programa → PARCEIRO INFRA — relatório de retorno",
        f"",
        f"Gerado em **{agora()}** (atualizado pelo watchdog a cada 10 min). Prazo: **20/10/2026 12:00**.",
        f"",
        f"## Execução",
        f"",
        f"| | |", f"|---|---|",
        f"| Início das transferências destes lotes | {inicio or '—'} |",
        f"| Lotes autorizados | {', '.join(lotes)} |",
        f"| Lotes concluídos (100% VALIDADO) | {', '.join(l for l, v in por_lote.items() if v['concluido']) or 'nenhum'} |",
        f"| Arquivos validados | **{num(val_arq)} de {num(tot_arq)}** ({100 * val_arq / tot_arq if tot_arq else 0:.1f}%) |",
        f"| Volume validado | **{gib(val_b)} de {gib(tot_b)} GiB** ({100 * val_b / tot_b if tot_b else 0:.1f}%) |",
        f"| Restante | {num(rest_arq)} arquivos · {gib(rest_b)} GiB |",
        f"| Ritmo na última hora | {num(b_1h[1])} arquivos · {gib(b_1h[0])} GiB validados |",
        f"| Previsão de conclusão dos lotes autorizados | {prev or 'sem dados da última hora'} |",
        f"| Margem até o prazo | {('%.1f h' % ((PRAZO - agora_dt).total_seconds() / 3600 - eta)) if eta is not None else '—'} |",
        f"",
        f"| Lote | Validados | Total | Estados |", f"|---|---:|---:|---|",
    ]
    for lt, v in por_lote.items():
        e = v["estados"]
        linhas.append(f"| {lt} | {num(e.get('VALIDADO', (0, 0))[0])} | {num(v['total'][0])} | "
                      f"{', '.join(f'{k} {n}' for k, (n, _) in sorted(e.items()))} |")
    linhas += [
        f"", f"## Saúde", f"",
        f"| | |", f"|---|---|",
        f"| Falhas transitórias (retentadas automaticamente) | {num(transit)} |",
        f"| Paradas globais (401 / cota) | {num(paradas)} |",
        f"| Intervenções do watchdog | {num(len(interv))} (detalhe em `logs/incidentes_migracao.csv`) |",
        f"| Problemas que exigem decisão | {', '.join(f'{k} {v}' for k, v in prob.items()) or 'nenhum'} |",
        f"", f"## Integridade", f"",
        f"Só é VALIDADO o arquivo relido **do PARCEIRO**, com tamanho e QuickXorHash iguais aos que a Microsoft calculou "
        f"na origem; SHA-256 registrado. Lista por arquivo: `output/migracao_parceiro/validados.csv`; por lote: "
        f"`resultado_lote_<LOTE>.csv`; problemas: `excecoes.csv`.",
        f"", f"## Pendências (fora desta execução)", f"",
        f"- Itens fora dos lotes autorizados, por estado: {', '.join(f'{k} {num(v)}' for k, v in sorted(pend.items()))}.",
        f"- Lotes preparados e **não aprovados**: {', '.join(pend_lotes) or 'nenhum'}.",
        f"- Respostas ao MP (14 e 16) e o vídeo de 87,8 GiB aguardam aprovação: `docs/migracao_parceiro/PENDENCIAS_MP_VIDEO.md`.",
        f"- `06. CERTIFICADORA ENTREGA` fora do escopo por decisão; 272 movidos em 09/10 fora desta demanda; nada é apagado do SharePoint.",
    ]
    if interv:
        linhas += ["", "## Últimas intervenções do watchdog", "", "| Quando | Situação | Ação | Motivo |", "|---|---|---|---|"]
        for i in interv[-15:]:
            linhas.append(f"| {i.get('quando')} | {i.get('situacao')} | {i.get('acao')} | {i.get('motivo', '')[:120]} |")
    out = saida(c)
    (out / "RELATORIO_RETORNO.md").write_text("\n".join(linhas) + "\n", encoding="utf-8")
    (out / "RELATORIO_RETORNO.json").write_text(json.dumps({
        "gerado_em": agora(), "lotes": {l: {"total": v["total"], "estados": v["estados"]} for l, v in por_lote.items()},
        "validados": [val_arq, val_b], "total": [tot_arq, tot_b], "previsao": prev, "falhas_transitorias": transit,
        "paradas_globais": paradas, "problemas": prob, "intervencoes": len(interv)}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"validados {val_arq}/{tot_arq} · {gib(val_b)}/{gib(tot_b)} GiB · previsao {prev} -> {out / 'RELATORIO_RETORNO.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
376_g2_consolidado - relatorios finais das quatro rodovias (SO LEITURA: banco G2 e CSVs ja gerados).

Para cada rodovia, uma conta SEM DUPLA CONTAGEM: cada arquivo da origem (inventario mais recente; para a RD-103,
o plano historico) cai em exatamente UMA situacao; o denominador e sempre dito.
  transferido e validado | transferido sem validacao | na fila | ja existia no PARCEIRO | duplicidade |
  nao aprovado | sem evidencia de entrega/aprovacao | equivalencia incerta | conflito concorrente |
  origem alterada | erro | fora do escopo
Mais a auditoria dos preexistentes (375) e a matriz unica de decisoes humanas, que junta:
  - casos da origem que dependem de decisao (sem evidencia, incerto, conflito, duplicidade, ja existente em outro
    destino, erro);
  - arquivos preexistentes no PARCEIRO com divergencia (nome, local, revisao, duplicidade, sem correspondencia).
Formato: O QUE ESTAVA NO PARCEIRO -> O QUE A EAP EXIGE -> DIVERGENCIA -> O QUE DEVERIA SER CORRIGIDO. Nada e corrigido.

Saida em output/migracao_parceiro_g2/:
  RELATORIO_<GO>.md                      um por rodovia
  RELATORIO_EXECUTIVO_G2_4_RODOVIAS.md   consolidado
  DECISOES_HUMANAS_G2_4_RODOVIAS.csv     filtravel por rodovia, tipo, gravidade, correcao proposta
  CONSOLIDADO_G2.json                    os numeros

Uso: python scripts/376_g2_consolidado.py
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2.base import agora, cfg, conectar, escrever_csv, gib, num, saida  # noqa: E402

SITUACOES = [
    ("TRANSFERIDO_VALIDADO", "Transferido e validado (hash relido do PARCEIRO)"),
    ("TRANSFERIDO_SEM_VALIDACAO", "Transferido, validacao pendente/divergente"),
    ("NA_FILA", "Elegivel, ainda na fila"),
    ("JA_EXISTIA_NO_PARCEIRO", "Ja existia no PARCEIRO (conteudo identico): nao copiado"),
    ("DUPLICIDADE", "Duplicidade na origem: nao copiado"),
    ("NAO_APROVADO", "Nao transferido: revisao nao aprovada / com ressalva"),
    ("SEM_EVIDENCIA", "Nao transferido: sem evidencia de entrega/aprovacao"),
    ("EQUIVALENCIA_INCERTA", "Nao transferido: correspondencia incerta"),
    ("CONFLITO", "Conflito: destino ocupado por terceiro com outro conteudo"),
    ("ORIGEM_ALTERADA", "Origem alterada durante a execucao"),
    ("ERRO", "Erro tecnico"),
    ("EXCLUIDO_CORTE_BM", "Enviado e depois EXCLUIDO do PARCEIRO: BM posterior ao mes-limite (29/10/2026)"),
    ("EXCLUIDO_WORD_COM_PDF", "Enviado e depois EXCLUIDO do PARCEIRO: Word com PDF de mesmo nome (30/10/2026)"),
    ("FORA_DO_ESCOPO", "Fora do escopo da EAP"),
]
GRAV = {"ALTA": 1, "MEDIA": 2, "BAIXA": 3, "": 4}


def situacao(cls: str, estado: str) -> str:
    if estado == "VALIDADO":
        return "TRANSFERIDO_VALIDADO"
    if estado in ("COPIADO", "DIVERGENTE"):
        return "TRANSFERIDO_SEM_VALIDACAO"
    if estado == "ERRO":
        return "ERRO"
    if estado in ("EXCLUIDO_CORTE_BM", "EXCLUIDO_WORD_COM_PDF"):     # nao e fila: saiu por decisao da Emmily
        return estado
    if cls == "AUSENTE_E_ELEGIVEL":
        return "NA_FILA"
    return {"JA_EXISTIA_EM_OUTRO_DESTINO": "JA_EXISTIA_NO_PARCEIRO", "JA_EXISTIA_CORRETO": "JA_EXISTIA_NO_PARCEIRO",
            "DUPLICIDADE_CONFIRMADA": "DUPLICIDADE", "NAO_APROVADO": "NAO_APROVADO", "SEM_EVIDENCIA": "SEM_EVIDENCIA",
            "EQUIVALENCIA_INCERTA": "EQUIVALENCIA_INCERTA", "CONFLITO_CONCORRENTE": "CONFLITO",
            "ALTERADO_DURANTE_EXECUCAO": "ORIGEM_ALTERADA"}.get(cls, "FORA_DO_ESCOPO")


def pct(a, b):
    return f"{100 * a / b:.2f}%".replace(".", ",") if b else "-"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    c = cfg()
    con = conectar()
    out = saida(c)
    decisoes, consolidado = [], {"quando": agora(), "rodovias": {}}
    for go, raiz in c["destino"]["arvores"].items():
        linhas = [dict(r) for r in con.execute(
            "SELECT p.*, o.caminho AS origem, o.tamanho, o.quick_xor_hash FROM plano p JOIN origem_item o "
            "USING (drive_id, item_id) WHERE p.rodovia=?", (go,))]
        if not linhas:
            consolidado["rodovias"][go] = {"planejado": False}
            continue
        # um arquivo de origem = uma situacao (se tiver mais de uma linha no plano, vale a mais avancada)
        ordem = [s for s, _ in SITUACOES]
        por_item: dict[tuple, dict] = {}
        for ln in linhas:
            s = situacao(ln["classificacao"], ln["estado"])
            k = (ln["drive_id"], ln["item_id"])
            if k not in por_item or ordem.index(s) < ordem.index(por_item[k]["sit"]):
                por_item[k] = {**ln, "sit": s}
        cont, vol = Counter(), Counter()
        for v in por_item.values():
            cont[v["sit"]] += 1
            vol[v["sit"]] += v["tamanho"] or 0
        total, total_b = sum(cont.values()), sum(vol.values())
        escopo = total - cont["FORA_DO_ESCOPO"]
        elegivel = sum(cont[s] for s in ("TRANSFERIDO_VALIDADO", "TRANSFERIDO_SEM_VALIDACAO", "NA_FILA",
                                         "JA_EXISTIA_NO_PARCEIRO", "CONFLITO", "ORIGEM_ALTERADA", "ERRO"))
        pre_j = out / f"auditoria_{go}" / "10_PREEXISTENTES_RESUMO.json"
        pre = json.loads(pre_j.read_text(encoding="utf-8")) if pre_j.exists() else None
        exc_pre = (0, 0)                                 # BMs de terceiros excluidos pelo corte (377 --preexistentes)
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='exclusao'").fetchone():
            exc_pre = con.execute("SELECT COUNT(*), COALESCE(SUM(tamanho), 0) FROM exclusao WHERE rodovia=? AND"
                                  " tipo='ARQUIVO_PREEXISTENTE' AND resultado='EXCLUIDO'", (go,)).fetchone()
        consolidado["rodovias"][go] = {"excluidos_bm_preexistentes": exc_pre[0], "excluidos_bm_preexistentes_bytes": exc_pre[1],
                                       "origem": total, "origem_bytes": total_b, "no_escopo": escopo,
                                       "elegivel": elegivel, "por_situacao": dict(cont),
                                       "bytes_por_situacao": dict(vol), "preexistentes": pre}
        # relatorio da rodovia
        md = [f"# {go} — migração G2 → PARCEIRO INFRA", "", f"Gerado em {agora()} (somente leitura do banco G2).", "",
              f"Árvore: `{raiz}`", "", "## Quantidades (cada arquivo da origem em UMA situação)", "",
              f"Denominador: **{num(total)} arquivos da origem** ({gib(total_b)} GiB). "
              f"No escopo da EAP: {num(escopo)} ({pct(escopo, total)}). "
              f"Elegíveis (entregues/aprovados, conforme as notas): {num(elegivel)} ({pct(elegivel, escopo)} do escopo).",
              "", "| Situação | Arquivos | % da origem | % do escopo | GiB |", "|---|---:|---:|---:|---:|"]
        for s, rot in SITUACOES:
            md.append(f"| {rot} | {num(cont[s])} | {pct(cont[s], total)} | "
                      f"{'-' if s == 'FORA_DO_ESCOPO' else pct(cont[s], escopo)} | {gib(vol[s])} |")
        md += ["", f"Validados sobre os elegíveis: **{num(cont['TRANSFERIDO_VALIDADO'])}/{num(elegivel)}** "
               f"({pct(cont['TRANSFERIDO_VALIDADO'], elegivel)}). "
               + ("Ainda há arquivos na fila: a migração desta rodovia NÃO está concluída." if cont["NA_FILA"] else ""), ""]
        if pre:
            md += ["## Arquivos que já estavam no PARCEIRO (estagiários/terceiros — autoria não atribuída)", "",
                   f"Foto do PARCEIRO nº {pre['foto']} (base desta etapa: foto nº {pre['foto_base']}). "
                   f"**{num(pre['preexistentes'])} arquivos** ({gib(pre['bytes'])} GiB), nenhum alterado por esta operação.", "",
                   "| Verificação | Arquivos |", "|---|---:|",
                   f"| Com alguma divergência | {num(pre['com_problema'])} |",
                   f"| Nome diferente do documento de origem | {num(pre['nome_nao_conforme'])} |",
                   f"| Fora da pasta prevista na EAP | {num(pre['local_nao_conforme'])} |",
                   f"| Revisão não aprovada presente | {num(pre['nao_aprovados'])} |",
                   f"| Duplicados dentro do PARCEIRO | {num(pre['duplicados'])} |", "",
                   "Correspondência com a origem: " + "; ".join(f"{k}: {num(v)}" for k, v in pre["por_correspondencia"].items()),
                   "", f"Detalhe: `auditoria_{go}/10_PREEXISTENTES_PARCEIRO.csv`.", ""]
        (out / f"RELATORIO_{go}.md").write_text("\n".join(md), encoding="utf-8")

        # decisoes: casos da origem
        for v in por_item.values():
            s = v["sit"]
            if s in ("TRANSFERIDO_VALIDADO", "NA_FILA", "NAO_APROVADO", "FORA_DO_ESCOPO", "EXCLUIDO_CORTE_BM",
                     "EXCLUIDO_WORD_COM_PDF"):
                continue
            grav = {"CONFLITO": "ALTA", "ERRO": "ALTA", "TRANSFERIDO_SEM_VALIDACAO": "ALTA", "ORIGEM_ALTERADA": "MEDIA",
                    "JA_EXISTIA_NO_PARCEIRO": "BAIXA"}.get(s, "MEDIA")
            no_parceiro = v["correspondente"] or ""
            correcao = {"SEM_EVIDENCIA": "obter a evidencia (entrega/aprovacao) e, com ela, autorizar a copia",
                        "EQUIVALENCIA_INCERTA": "decidir manualmente a correspondencia/destino",
                        "JA_EXISTIA_NO_PARCEIRO": ("nenhuma copia; se o local atual divergir da EAP, autorizar mover "
                                               "para o destino da EAP"),
                        "DUPLICIDADE": "nenhuma acao (mesmo conteudo de outro item do plano)",
                        "CONFLITO": "comparar as duas versoes e decidir qual vale (nada foi sobrescrito)",
                        "ORIGEM_ALTERADA": "replanejar o item com a versao atual da origem",
                        "ERRO": "reprocessar o item", "TRANSFERIDO_SEM_VALIDACAO": "revalidar o hash"}.get(s, "")
            decisoes.append((go, "ORIGEM", s, grav, no_parceiro or "(ausente no PARCEIRO)", v["destino_rel"] or "(sem destino: "
                             "nao elegivel)", (v["motivo"] or "")[:300], correcao, v["evidencia"] or "",
                             v["origem"], v["item_id"], v["fileid_destino"] or "", v["tamanho"]))
        # decisoes: preexistentes
        pcsv = out / f"auditoria_{go}" / "10_PREEXISTENTES_PARCEIRO.csv"
        if pcsv.exists():
            with open(pcsv, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f, delimiter=";"):
                    if r["decisao_humana"] != "SIM":
                        continue
                    exige = (f"{r['pasta_recomendada']}/{r['nome_recomendado']}" if r["nome_recomendado"] else
                             f"classificacao na EAP: {r['classificacao_eap'] or 'sem correspondencia'}")
                    tipo = ("REVISAO_NAO_APROVADA" if r["classificacao_eap"] == "NAO_APROVADO" else
                            "NOME_E_LOCAL" if r["conformidade_nomenclatura"] == "NAO_CONFORME" and
                            r["conformidade_localizacao"] == "NAO_CONFORME" else
                            "NOMENCLATURA" if r["conformidade_nomenclatura"] == "NAO_CONFORME" else
                            "LOCALIZACAO" if r["conformidade_localizacao"] == "NAO_CONFORME" else
                            "DUPLICADO_NO_PARCEIRO" if r["duplicado_no_parceiro"] == "SIM" else
                            "SEM_CORRESPONDENCIA" if r["correspondencia_origem"] == "SEM_CORRESPONDENCIA" else
                            "OUTRO")
                    decisoes.append((go, "PREEXISTENTE_PARCEIRO", tipo, r["prioridade"],
                                     f"{r['pasta_encontrada']}/{r['nome_encontrado']}", exige, r["problema"],
                                     r["correcao_recomendada"], r["evidencia"], r["origem_caminho"],
                                     r["origem_item_id"], r["fileid"], r["tamanho"]))

    decisoes.sort(key=lambda d: (d[0], GRAV.get(d[3], 4), d[2], d[4]))
    cab = ["rodovia", "origem_do_caso", "tipo_problema", "gravidade", "o_que_estava_no_parceiro", "o_que_a_eap_exige",
           "divergencia", "correcao_proposta_NAO_EXECUTADA", "evidencia", "origem_caminho", "origem_item_id",
           "parceiro_fileid", "tamanho"]
    escrever_csv(out / "DECISOES_HUMANAS_G2_4_RODOVIAS.csv", cab, decisoes)
    consolidado["decisoes"] = dict(Counter((d[0], d[1], d[2]) for d in decisoes).most_common()) and \
        {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in Counter((d[0], d[1], d[2]) for d in decisoes).items()}
    (out / "CONSOLIDADO_G2.json").write_text(json.dumps(consolidado, ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# Migração G2 → PARCEIRO INFRA — consolidado das quatro rodovias", "", f"Gerado em {agora()}.", "",
          "| Rodovia | Origem | No escopo | Elegíveis | Validados | % dos elegíveis | Já existiam | % coberto (validados + já existiam) | Na fila | "
          "Sem evidência | Não aprovados | Conflitos | Erros | GiB validados | Preexistentes no PARCEIRO | BM excluídos (corte: nossos + preexistentes) |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for go, d in consolidado["rodovias"].items():
        if not d.get("origem"):
            md.append(f"| {go} | não planejada | | | | | | | | | | | | | | |")
            continue
        s = d["por_situacao"]
        md.append(f"| {go} | {num(d['origem'])} | {num(d['no_escopo'])} | {num(d['elegivel'])} | "
                  f"{num(s.get('TRANSFERIDO_VALIDADO', 0))} | {pct(s.get('TRANSFERIDO_VALIDADO', 0), d['elegivel'])} | "
                  f"{num(s.get('JA_EXISTIA_NO_PARCEIRO', 0))} | "
                  f"{pct(s.get('TRANSFERIDO_VALIDADO', 0) + s.get('JA_EXISTIA_NO_PARCEIRO', 0), d['elegivel'])} | "
                  f"{num(s.get('NA_FILA', 0))} | {num(s.get('SEM_EVIDENCIA', 0))} | "
                  f"{num(s.get('NAO_APROVADO', 0))} | {num(s.get('CONFLITO', 0))} | {num(s.get('ERRO', 0))} | "
                  f"{gib(d['bytes_por_situacao'].get('TRANSFERIDO_VALIDADO', 0))} | "
                  f"{num(d['preexistentes']['preexistentes']) if d.get('preexistentes') else '-'} | "
                  f"{num(s.get('EXCLUIDO_CORTE_BM', 0))} + {num(d['excluidos_bm_preexistentes'])} |")
    md += ["", f"Casos para decisão humana: **{num(len(decisoes))}** em `DECISOES_HUMANAS_G2_4_RODOVIAS.csv` "
           "(filtrar por rodovia, tipo_problema, gravidade). Nenhuma correção foi executada.", "",
           "Relatórios por rodovia: `RELATORIO_<GO>.md`. Nenhum percentual acima afirma 100% sem o número que o sustenta."]
    (out / "RELATORIO_EXECUTIVO_G2_4_RODOVIAS.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

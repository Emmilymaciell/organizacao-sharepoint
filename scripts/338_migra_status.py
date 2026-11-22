"""
338_migra_status - painel da migracao (inventario, plano, transferencia,
conferencia), por QUANTIDADE e por BYTES, a partir do banco local.
Nao consulta SharePoint nem PARCEIRO; nao recalcula o inventario.

Grava output/migracao_parceiro/status.json e imprime o resumo.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_parceiro.base import agora, cfg, conectar, escrever_csv, gib, num, saida  # noqa: E402

ORDEM = ["FORA_DO_ESCOPO", "PENDENTE_DE_DECISAO", "AGUARDANDO_APROVACAO", "CONFLITO", "EXCECAO", "ESGOTADO",
         "APROVADO", "EM_TRANSFERENCIA", "FALHOU", "ORIGEM_ALTERADA", "COPIADO", "EM_VERIFICACAO", "DIVERGENTE",
         "VALIDADO", "HOMOLOGADO"]
PROBLEMAS = ("CONFLITO", "EXCECAO", "ESGOTADO", "FALHOU", "ORIGEM_ALTERADA", "DIVERGENTE")


def main() -> int:
    lote_alvo = sys.argv[sys.argv.index("--lote") + 1] if "--lote" in sys.argv else None
    c = cfg()
    con = conectar()
    inv = con.execute("SELECT COUNT(*) n, COALESCE(SUM(tamanho),0) b FROM origem_item "
                      "WHERE eh_pasta=0 AND ausente_desde IS NULL").fetchone()
    por_estado = {r["estado"]: {"arquivos": r["n"], "bytes": r["b"]} for r in con.execute(
        "SELECT p.estado, COUNT(*) n, COALESCE(SUM(o.tamanho),0) b FROM plano_item p "
        "JOIN origem_item o USING(item_id) GROUP BY p.estado")}
    escopo = {r["escopo"]: {"arquivos": r["n"], "bytes": r["b"]} for r in con.execute(
        "SELECT p.escopo, COUNT(*) n, COALESCE(SUM(o.tamanho),0) b FROM plano_item p "
        "JOIN origem_item o USING(item_id) GROUP BY p.escopo")}
    por_pasta = [dict(r) for r in con.execute(
        "SELECT substr(o.caminho_rel, 1, instr(o.caminho_rel, '/') - 1) pasta, COUNT(*) arquivos, "
        "SUM(o.tamanho) bytes, SUM(p.estado='VALIDADO') validados, "
        "SUM(CASE WHEN p.estado='VALIDADO' THEN o.tamanho ELSE 0 END) bytes_validados "
        "FROM plano_item p JOIN origem_item o USING(item_id) GROUP BY 1 ORDER BY 1")]
    lotes = [dict(r) for r in con.execute("SELECT nome, piloto, arquivos, bytes, aprovado_em FROM lote")]
    prog_arq = saida(c) / "progresso.json"
    progresso = json.loads(prog_arq.read_text(encoding="utf-8")) if prog_arq.exists() else None
    no_escopo = escopo.get("migrar", {"arquivos": 0, "bytes": 0})
    valid = por_estado.get("VALIDADO", {"arquivos": 0, "bytes": 0})
    d = {"gerado_em": agora(), "inventario": dict(inv), "escopo": escopo, "por_estado": por_estado,
         "por_pasta_origem": por_pasta, "lotes": lotes, "transferencia_em_curso": progresso,
         "integridade": {"validados": valid, "no_escopo": no_escopo,
                         "pct_arquivos": round(100 * valid["arquivos"] / no_escopo["arquivos"], 2)
                         if no_escopo["arquivos"] else 0,
                         "pct_bytes": round(100 * valid["bytes"] / no_escopo["bytes"], 2) if no_escopo["bytes"] else 0}}
    (saida(c) / "status.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")

    # fila de excecoes: tudo que precisa de olho humano, com o ultimo motivo registrado
    marc = ",".join("?" * len(PROBLEMAS))
    exc = con.execute(
        f"SELECT p.estado, p.lote, o.caminho_rel, p.destino_pasta || '/' || p.nome_final, o.tamanho, "
        f"(SELECT detalhe FROM evento e WHERE e.item_id=p.item_id ORDER BY e.id DESC LIMIT 1) "
        f"FROM plano_item p JOIN origem_item o USING(item_id) WHERE p.estado IN ({marc}) "
        f"ORDER BY p.estado, o.caminho_rel", PROBLEMAS).fetchall()
    escrever_csv(saida(c) / "excecoes.csv", ["estado", "lote", "origem", "destino", "bytes", "ultimo_motivo"],
                 (tuple(r) for r in exc))
    sql_reg = (
        "SELECT p.lote, p.estado, o.caminho_rel, p.destino_pasta || '/' || p.nome_final, p.nome_final, o.tamanho, "
        "o.quick_xor_hash, v.qxh_destino, v.sha256_destino, v.sha256_origem, v.metodo, v.resultado, v.quando, "
        "(SELECT MIN(inicio) FROM transferencia t WHERE t.item_id=p.item_id), "
        "(SELECT ROUND(SUM(segundos),1) FROM transferencia t WHERE t.item_id=p.item_id), "
        "(SELECT COUNT(*) FROM transferencia t WHERE t.item_id=p.item_id), v.detalhe "
        "FROM plano_item p JOIN origem_item o USING(item_id) LEFT JOIN verificacao v "
        "ON v.id = (SELECT MAX(id) FROM verificacao WHERE item_id=p.item_id) ")
    cab_reg = ["lote", "estado", "origem", "destino_parceiro", "nome_definitivo", "bytes", "quickxor_origem_servidor",
               "quickxor_destino_lido", "sha256_destino_lido", "sha256_origem_lido", "metodo_conferencia",
               "resultado_conferencia", "conferido_em", "copia_iniciada_em", "segundos_copia", "tentativas",
               "divergencia"]
    validados = con.execute(sql_reg + "WHERE p.estado='VALIDADO' ORDER BY p.lote, o.caminho_rel").fetchall()
    escrever_csv(saida(c) / "validados.csv", cab_reg, (tuple(r) for r in validados))
    if lote_alvo:
        regs = con.execute(sql_reg + "WHERE p.lote=? ORDER BY o.caminho_rel", (lote_alvo,)).fetchall()
        escrever_csv(saida(c) / f"resultado_lote_{lote_alvo}.csv", cab_reg, (tuple(r) for r in regs))
        resumo_lote: dict[str, list[int]] = {}
        for r in regs:
            s = resumo_lote.setdefault(r[1], [0, 0])
            s[0] += 1
            s[1] += r[5] or 0
        d_lote = {"lote": lote_alvo, "gerado_em": agora(), "arquivos": len(regs),
                  "bytes": sum(r[5] or 0 for r in regs),
                  "por_estado": {k: {"arquivos": v[0], "bytes": v[1]} for k, v in resumo_lote.items()},
                  "todos_validados": all(r[1] == "VALIDADO" for r in regs)}
        (saida(c) / f"resultado_lote_{lote_alvo}.json").write_text(
            json.dumps(d_lote, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Resultado consolidado do lote {lote_alvo}: {d_lote['por_estado']} -> "
              f"output/migracao_parceiro/resultado_lote_{lote_alvo}.csv")

    print(f"Inventario: {num(inv['n'])} arquivos · {gib(inv['b'])} GiB")
    for e in ORDEM:
        if e in por_estado:
            v = por_estado[e]
            print(f"  {e:22} {num(v['arquivos']):>7} arq · {gib(v['bytes']):>8} GiB")
    print(f"Integridade comprovada: {d['integridade']['pct_arquivos']}% dos arquivos e "
          f"{d['integridade']['pct_bytes']}% dos bytes do escopo 'migrar'")
    for lt in lotes:
        print(f"  lote {lt['nome']}: {lt['arquivos']} arq · {gib(lt['bytes'])} GiB · "
              f"aprovado {lt['aprovado_em'] or 'NAO'}")
    print(f"Excecoes para revisao: {num(len(exc))} (output/migracao_parceiro/excecoes.csv) · "
          f"validados: {num(len(validados))} (output/migracao_parceiro/validados.csv)")
    if progresso:
        a = progresso["arquivos"]
        print(f"Ultima transferencia ({progresso.get('lote')}, {progresso['atualizado_em']}): "
              f"{a['feitos']}/{a['total']} arq · {progresso['pct_bytes']}% bytes · "
              f"{progresso['vel_media_MBps']} MB/s media · ETA {progresso['eta_s']} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

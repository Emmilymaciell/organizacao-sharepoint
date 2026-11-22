"""
368_g2_status - painel SO LEITURA da operacao G2: progresso por categoria e estado, vazao, executor ativo,
pausa, conflitos, governador de recursos (estado, motivo, tempo trabalhando x aguardando) e ultimas linhas do
log. Grava tambem output/migracao_parceiro_g2/status.json.

Uso: python scripts/368_g2_status.py                    (resumo das 4 rodovias + recursos)
     python scripts/368_g2_status.py --rodovia RD-101   (detalhe de uma rodovia)
     python scripts/368_g2_status.py --rodovia RD-101 --conflitos | --falhas
     python scripts/368_g2_status.py --recursos         (historico do governador)
"""

from __future__ import annotations

import argparse
import json
import msvcrt
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2.base import agora, cfg, conectar, gib, num, pasta, saida  # noqa: E402


def executor_ativo(c) -> bool:
    p = pasta("tmp", c) / "EXECUTOR.lock"
    if not p.exists():
        return False
    with open(p, "r+") as f:
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            return False
        except OSError:
            return True


def tempos_governador(con) -> dict:
    """Segundos em cada estado do governador (intervalos entre linhas consecutivas da tabela recurso, por
    execucao; intervalos > 10 min nao contam: o executor estava fora)."""
    from datetime import datetime
    linhas = con.execute("SELECT em, estado FROM recurso ORDER BY em").fetchall()
    out: dict[str, float] = {}
    for (t1, e1), (t2, _) in zip(linhas, linhas[1:]):
        dt = (datetime.fromisoformat(t2) - datetime.fromisoformat(t1)).total_seconds()
        if 0 <= dt <= 600:
            out[e1] = out.get(e1, 0) + dt
    return out


def painel_recursos(c, con, historico: bool) -> dict:
    gj = pasta("tmp", c) / "GOVERNADOR.json"
    g = json.loads(gj.read_text(encoding="utf-8")) if gj.exists() else {}
    t = tempos_governador(con)
    trab = sum(v for k, v in t.items() if k in ("EXECUTANDO", "REDUZINDO_CARGA", "RETOMANDO"))
    esp = sum(v for k, v in t.items() if k not in ("EXECUTANDO", "REDUZINDO_CARGA", "RETOMANDO"))
    if g:
        l = g.get("leitura", {})
        print(f"governador ({g.get('em')}): {g.get('estado')} | workers {g.get('workers')} | ativos {g.get('ativos')}"
              f" | {g.get('motivo')}")
        print(f"  RAM livre {l.get('ram_livre_mb')} MB | commit {l.get('commit_pct')}% | CPU {l.get('cpu_pct')}% | "
              f"projeto prioritario {l.get('prioritario_proc')} proc / {l.get('prioritario_ws_mb')} MB | disco livre "
              f"{l.get('disco_livre_gib')} GiB")
    print(f"  tempo trabalhando {trab / 3600:.1f} h | aguardando recursos/pausado {esp / 3600:.1f} h")
    if historico:
        print("  ultimas mudancas de estado:")
        ant = None
        for em, est, mot, ram in con.execute("SELECT em, estado, motivo, ram_livre_mb FROM recurso ORDER BY em DESC"
                                             " LIMIT 400").fetchall()[::-1]:
            if est != ant:
                print(f"    {em} {est:32s} RAM {ram} MB :: {mot}")
                ant = est
    return {"governador": g, "segundos_por_estado": t}


def resumo_geral(c, con) -> list[dict]:
    out = []
    for go in c["destino"]["arvores"]:
        r = con.execute("""SELECT COUNT(*), SUM(o.tamanho),
                 SUM(p.estado='VALIDADO'), SUM(CASE WHEN p.estado='VALIDADO' THEN o.tamanho END),
                 SUM(p.estado='PULADO'), SUM(p.estado IN ('ERRO','DIVERGENTE'))
               FROM plano p JOIN origem_item o USING (drive_id, item_id) WHERE p.rodovia=? AND p.classificacao IN
               ('AUSENTE_E_ELEGIVEL','CONFLITO_CONCORRENTE','ALTERADO_DURANTE_EXECUCAO','JA_EXISTIA_CORRETO')""",
                        (go,)).fetchone()
        pend = con.execute("SELECT COUNT(*) FROM plano WHERE rodovia=? AND classificacao IN ('SEM_EVIDENCIA',"
                           "'EQUIVALENCIA_INCERTA','DUPLICIDADE_CONFIRMADA','JA_EXISTIA_EM_OUTRO_DESTINO')", (go,)).fetchone()[0]
        d = {"rodovia": go, "previstos": r[0] or 0, "bytes": r[1] or 0, "validados": r[2] or 0,
             "validados_bytes": r[3] or 0, "pulados": r[4] or 0, "erros": r[5] or 0, "pendentes_decisao": pend}
        out.append(d)
        pct = 100 * d["validados"] / d["previstos"] if d["previstos"] else 0
        print(f"  {go:8s} VALIDADO {num(d['validados']):>6s}/{num(d['previstos']):<6s} ({pct:5.1f}%) "
              f"{gib(d['validados_bytes']):>7s}/{gib(d['bytes']):<7s} GiB | pulados {num(d['pulados'])} | "
              f"erros {num(d['erros'])} | pendentes de decisao {num(pend)}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rodovia")
    ap.add_argument("--conflitos", action="store_true")
    ap.add_argument("--falhas", action="store_true")
    ap.add_argument("--recursos", action="store_true")
    a = ap.parse_args()
    c = cfg()
    con = conectar()
    if not a.rodovia:
        print(f"G2 | fase {c['fase']} | liberadas {c['destino'].get('rodovias_liberadas')} | executor "
              f"{'ATIVO' if executor_ativo(c) else 'parado'}"
              f"{' | PAUSADO MANUALMENTE (tmp/migracao_parceiro_g2/PAUSAR)' if (pasta('tmp', c) / 'PAUSAR').exists() else ''}")
        geral = resumo_geral(c, con)
        rec = painel_recursos(c, con, a.recursos)
        (saida(c) / "status.json").write_text(json.dumps({"quando": agora(), "fase": c["fase"], "rodovias": geral,
                                                          **rec}, ensure_ascii=False, indent=1, default=str),
                                              encoding="utf-8")
        log = pasta("logs", c) / "LOG_OPERACIONAL_G2.log"
        if log.exists():
            print("log:")
            for linha in log.read_text(encoding="utf-8").splitlines()[-6:]:
                print("  " + linha[:200])
        return 0
    q ="""SELECT p.categoria, p.estado, COUNT(*) n, SUM(o.tamanho) b FROM plano p JOIN origem_item o
           USING (drive_id, item_id) WHERE p.rodovia=? AND p.classificacao IN
           ('AUSENTE_E_ELEGIVEL','CONFLITO_CONCORRENTE','ALTERADO_DURANTE_EXECUCAO','JA_EXISTIA_CORRETO')
           GROUP BY 1, 2 ORDER BY 1, 2"""
    linhas = [dict(r) for r in con.execute(q, (a.rodovia,))]
    tot = {}
    for r in linhas:
        tot.setdefault(r["estado"], [0, 0])
        tot[r["estado"]][0] += r["n"]
        tot[r["estado"]][1] += r["b"] or 0
    prev = sum(v[0] for v in tot.values())
    prevb = sum(v[1] for v in tot.values())
    val = tot.get("VALIDADO", [0, 0])
    ult = con.execute("SELECT MIN(inicio), MAX(fim), SUM(bytes), SUM(segundos) FROM envio WHERE resultado='CRIADO'"
                      " AND inicio > datetime('now','-1 hour')").fetchone()
    st = {"quando": agora(), "fase": c["fase"], "rodovia": a.rodovia, "executor_ativo": executor_ativo(c),
          "pausado": (pasta("tmp", c) / "PAUSAR").exists(), "previstos": prev, "previstos_bytes": prevb,
          "por_estado": tot, "validados": val[0], "validados_bytes": val[1],
          "pct_arquivos": round(100 * val[0] / prev, 2) if prev else 0,
          "pct_bytes": round(100 * val[1] / prevb, 2) if prevb else 0}
    (saida(c) / "status.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"G2 {a.rodovia} | fase {c['fase']} | executor {'ATIVO' if st['executor_ativo'] else 'parado'}"
          f"{' | PAUSADO (tmp/migracao_parceiro_g2/PAUSAR)' if st['pausado'] else ''}")
    print(f"VALIDADO {num(val[0])}/{num(prev)} arquivos ({st['pct_arquivos']}%) | {gib(val[1])}/{gib(prevb)} GiB "
          f"({st['pct_bytes']}%)")
    for e, (n, b) in sorted(tot.items()):
        print(f"  {e:12s} {num(n):>7s} {gib(b):>8s} GiB")
    print("por categoria:")
    cats = {}
    for r in linhas:
        cats.setdefault(r["categoria"], {})[r["estado"]] = r["n"]
    for k, v in sorted(cats.items()):
        print(f"  {k:14s} {v}")
    if ult and ult[2]:
        print(f"ultima hora: {gib(ult[2])} GiB enviados")
    if a.conflitos or a.falhas:
        est = ("PULADO",) if a.conflitos else ("ERRO", "DIVERGENTE", "NA_FILA")
        for r in con.execute(f"SELECT estado, classificacao, destino_rel, erro FROM plano WHERE rodovia=? AND estado IN "
                             f"({','.join('?' * len(est))})", (a.rodovia, *est)):
            print(f"  [{r[0]}/{r[1]}] {r[2][-110:]}\n      {r[3]}")
    log = pasta("logs", c) / "LOG_OPERACIONAL_G2.log"
    if log.exists():
        print("log:")
        for linha in log.read_text(encoding="utf-8").splitlines()[-6:]:
            print("  " + linha[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

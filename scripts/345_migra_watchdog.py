"""
345_migra_watchdog - supervisor da migracao, uma checagem por execucao (o Agendador roda a cada 10 min).

Coleta evidencias (processos, batimento do motor no banco, log da fila, progresso, PAUSAR, disco,
fila retentavel, historico de reinicios), decide com migra_parceiro/watchdog.decidir() e:
  - SAUDAVEL/OBSERVAR/CONCLUIDO/PAUSADO/AGUARDAR: nao interfere;
  - REINICIAR: dispara a tarefa existente "Programa Migracao PARCEIRO" (IgnoreNew: nunca duas instancias);
  - TRAVADO: encerra SO a arvore do runner da migracao e dispara a tarefa (retoma pelos checkpoints;
    um envio incerto e conferido por hash no destino antes de repetir - nada e sobrescrito);
  - IMPEDIMENTO/DUPLICADO: so registra (precisa de pessoa).
Sempre: linha em logs/watchdog.log, incidente em logs/incidentes_migracao.csv quando nao SAUDAVEL,
e regenera output/migracao_parceiro/RELATORIO_RETORNO.md.

Nunca: apaga arquivo, mexe em credencial, muda fase/config/lotes, inicia lote nao autorizado.

Uso: python scripts/345_migra_watchdog.py --lotes "PILOTO-02,..." [--simular]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_parceiro.base import BRT, agora, cfg  # noqa: E402
from migra_parceiro.watchdog import Evidencias, decidir  # noqa: E402

TAREFA = "Programa Migracao PARCEIRO"
LOG = RAIZ / "logs" / "watchdog.log"
INC = RAIZ / "logs" / "incidentes_migracao.csv"
CAB = ["quando", "situacao", "acao", "motivo", "lote_atual", "runner_pids", "batimento_min", "log_min",
       "progresso_min", "disco_livre_gib", "retentaveis", "reinicios_24h", "humano", "resultado"]


def ps(cmd: str) -> str:
    return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cmd],
                          capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def runners() -> list[int]:
    # so processos que EXECUTAM o 339 (-File ...339_migra_rodar_lote.ps1); exclui esta propria consulta,
    # cuja linha de comando tambem contem o nome do script
    out = ps("Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | "
             "Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -like '*-File*339_migra_rodar_lote.ps1*' "
             "-and $_.CommandLine -notlike '*Get-CimInstance*' } | "
             "ForEach-Object { $_.ProcessId }")
    return [int(x) for x in out.split() if x.strip().isdigit()]


def idade_min(p: Path) -> float | None:
    return (time.time() - p.stat().st_mtime) / 60 if p.exists() else None


def ler_incidentes() -> list[dict]:
    if not INC.exists():
        return []
    with open(INC, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def registrar_incidente(linha: dict):
    novo = not INC.exists()
    with open(INC, "a", encoding="utf-8-sig" if novo else "utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAB, delimiter=";")
        if novo:
            w.writeheader()
        w.writerow({k: linha.get(k, "") for k in CAB})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--lotes", required=True)
    ap.add_argument("--simular", action="store_true", help="decide e registra, mas nao age")
    a = ap.parse_args()
    lotes = [x.strip() for x in a.lotes.split(",") if x.strip()]
    c = cfg()

    con = sqlite3.connect(f"file:{RAIZ / c['banco']}?mode=ro", uri=True, timeout=60)
    trava = con.execute("SELECT pid, batida FROM trava").fetchone()
    batimento = (time.time() - trava[1]) / 60 if trava else None
    marc = ",".join("?" * len(lotes))
    retentaveis = con.execute(f"SELECT COUNT(*) FROM plano_item WHERE lote IN ({marc}) AND estado IN "
                              f"('APROVADO','FALHOU','EM_TRANSFERENCIA','COPIADO','EM_VERIFICACAO')", lotes).fetchone()[0]
    con.close()

    logs = sorted((RAIZ / "logs").glob("migra_fila_*.log"), key=lambda p: p.stat().st_mtime)
    ultimo_log = logs[-1] if logs else None
    log_min = idade_min(ultimo_log) if ultimo_log else None
    texto_log = ultimo_log.read_text(encoding="utf-8", errors="replace")[-20000:] if ultimo_log else ""
    parada_global = "PARADA GLOBAL" in texto_log
    prog = RAIZ / c["saida"] / "progresso.json"
    lote_atual = ""
    try:
        lote_atual = json.loads(prog.read_text(encoding="utf-8")).get("lote", "")
    except Exception:
        pass
    pausar = (RAIZ / c["espaco"]["tmp"] / "PAUSAR").exists()
    livre = shutil.disk_usage(RAIZ.anchor).free / 2**30
    reserva = float(c["espaco"]["reserva_minima_gib"])

    agora_dt = datetime.now(BRT)
    inc = ler_incidentes()
    reinicios = [i for i in inc if i.get("acao") in ("reiniciar", "encerrar_e_reiniciar") and i.get("resultado") == "executado"]
    rec = [i for i in reinicios if datetime.fromisoformat(i["quando"]) >= agora_dt - timedelta(hours=24)]
    ultimo = (agora_dt - datetime.fromisoformat(rec[-1]["quando"])).total_seconds() / 60 if rec else None

    pids = runners()
    e = Evidencias(runners_vivos=len(pids), batimento_min=batimento, log_min=log_min, progresso_min=idade_min(prog),
                   pausar=pausar, parada_global=parada_global, disco_livre_gib=livre, reserva_gib=reserva,
                   retentaveis=retentaveis, reinicios_24h=len(rec), ultimo_reinicio_min=ultimo)
    d = decidir(e)

    resultado = "-"
    if not a.simular and d.acao in ("reiniciar", "encerrar_e_reiniciar"):
        if d.acao == "encerrar_e_reiniciar":
            for pid in pids:   # so a arvore do runner da migracao (339 -> python -> rclone da migracao)
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            time.sleep(15)
        r = subprocess.run(["schtasks", "/Run", "/TN", TAREFA], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        resultado = "executado" if r.returncode == 0 else f"falhou: {(r.stdout + r.stderr).strip()[:200]}"
    elif a.simular and d.acao != "nenhuma":
        resultado = "simulado"

    linha = {"quando": agora(), "situacao": d.situacao, "acao": d.acao, "motivo": d.motivo, "lote_atual": lote_atual,
             "runner_pids": " ".join(map(str, pids)), "batimento_min": f"{batimento:.1f}" if batimento is not None else "",
             "log_min": f"{log_min:.1f}" if log_min is not None else "",
             "progresso_min": f"{e.progresso_min:.1f}" if e.progresso_min is not None else "",
             "disco_livre_gib": f"{livre:.1f}", "retentaveis": retentaveis, "reinicios_24h": len(rec),
             "humano": "SIM" if d.humano else "", "resultado": resultado}
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(" | ".join(f"{k}={v}" for k, v in linha.items()) + "\n")
    if d.situacao != "SAUDAVEL" and not a.simular:
        registrar_incidente(linha)
    if not a.simular:
        subprocess.run([sys.executable, str(RAIZ / "scripts" / "344_migra_relatorio_retorno.py"), "--lotes", a.lotes],
                       capture_output=True)
    print(f"{d.situacao} | {d.acao} | {d.motivo} | resultado={resultado}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

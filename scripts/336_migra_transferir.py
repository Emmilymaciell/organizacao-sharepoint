"""
336_migra_transferir - executa um lote APROVADO: SharePoint -> PARCEIRO INFRA.

ESCREVE NO PARCEIRO. So roda com:
  - config/migracao_parceiro.yaml em fase 'piloto' (so lotes piloto) ou 'execucao';
  - lote aprovado por escrito (335_migra_lote.py aprovar);
  - a trava de execucao unica livre.

Copia arquivo a arquivo, direto para o caminho e nome FINAIS, com o rclone
rcd subido por scripts/rcparceiro.ps1 (credenciais e travas ficam la). Nunca
move, apaga ou sobrescreve. Retomavel: rode de novo o mesmo comando.

Controles durante a execucao:
  - pausar: criar o arquivo tmp/migracao_parceiro/PAUSAR (termina os em voo e para)
  - progresso: output/migracao_parceiro/progresso.json (atualizado a cada 5 s)
  - disco: para de iniciar arquivos se o C: cair abaixo da reserva do config

Uso:
    python scripts/336_migra_transferir.py PILOTO-01 [--concorrencia 4]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_parceiro.base import abrir_execucao, cfg, conectar, fechar_execucao, saida  # noqa: E402
from migra_parceiro.espaco import Guarda  # noqa: E402
from migra_parceiro.motor import Motor, Trava  # noqa: E402
from migra_parceiro.rc import Rcd  # noqa: E402


def manter_acordado(ligar: bool) -> None:
    """Impede a SUSPENSAO do Windows so enquanto este processo transfere (SetThreadExecutionState).
    Nao altera o plano de energia: ao terminar (ou cair), o Windows volta ao normal sozinho.
    Nao impede suspensao por fechar a tampa nem por comando manual."""
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if ligar else 0))
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("lote")
    ap.add_argument("--concorrencia", type=int)
    a = ap.parse_args()

    c = cfg()
    con = conectar()
    lt = con.execute("SELECT * FROM lote WHERE nome=?", (a.lote,)).fetchone()
    if not lt or not lt["aprovado_em"]:
        print(f"BLOQUEADO: lote {a.lote} inexistente ou sem aprovacao escrita")
        return 1
    fase = c.get("fase")
    if fase not in ("piloto", "execucao") or (fase == "piloto" and not lt["piloto"]):
        print(f"BLOQUEADO: fase={fase!r} nao permite este lote (piloto={lt['piloto']})")
        return 1

    t = c["transferencia"]
    conc = a.concorrencia or int(t["concorrencia"])
    guarda = Guarda.do_config(RAIZ, c)
    tmp = RAIZ / c["espaco"]["tmp"]
    tmp.mkdir(parents=True, exist_ok=True)
    flags = [str(f) for f in (t.get("flags_rclone") or [])] + ["--temp-dir", str(tmp / "rclone")]
    (tmp / "rclone").mkdir(exist_ok=True)
    log = RAIZ / "logs" / f"rclone_migra_{a.lote}.log"

    ex_id = abrir_execucao(con, Path(__file__).name, {"lote": a.lote, "concorrencia": conc, "flags": flags})
    manter_acordado(True)
    try:
        with Trava(con), Rcd(flags=flags, log=log) as rcd:
            m = Motor(con, rcd, c["remotes"]["origem"], c["remotes"]["destino"], concorrencia=conc,
                      max_tentativas=int(t["max_tentativas"]), guarda=guarda,
                      arquivo_progresso=saida(c) / "progresso.json", arquivo_pausa=tmp / "PAUSAR",
                      execucao_id=ex_id,
                      concorrencia_min=int(t.get("concorrencia_min") or conc),
                      concorrencia_max=int(t.get("concorrencia_max") or conc) if not a.concorrencia else conc,
                      tentativas_por_arquivo=int(t.get("tentativas_por_arquivo") or 3),
                      memoria_max_mb=int(t.get("memoria_max_mb") or 0),
                      janela_ajuste_s=float(t.get("janela_ajuste_s") or 90),
                      conferir_em_linha=bool(t.get("conferir_em_linha")),
                      log=lambda s: print(s, flush=True))
            r = m.transferir(a.lote)
    finally:
        manter_acordado(False)
    estado = "interrompida" if (r["pausado"] or r["parado_por_disco"] or r["parado_por_autenticacao"]) else "concluida"
    fechar_execucao(con, ex_id, estado, r)
    print(r)
    if r["parado_por_autenticacao"]:
        return 3          # o runner para: credencial precisa ser renovada por uma pessoa
    print("Proximo passo: python scripts/337_migra_conferir.py", a.lote)
    return 0 if estado == "concluida" and not r["falhas"] else 2


if __name__ == "__main__":
    # Codigos de saida (lidos pelo 339): 0 ok | 1 BLOQUEADO (fase, aprovacao, outra execucao ativa)
    # 2 falhas/pausa (retomavel) | 3 parada global (credencial/cota) | 5 erro inesperado (retomavel)
    try:
        raise SystemExit(main())
    except RuntimeError as e:          # Trava: ja ha execucao ativa
        print(f"BLOQUEADO: {e}")
        raise SystemExit(1)
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        print("ERRO INESPERADO: a fila esta no banco; rodar de novo retoma de onde parou.")
        raise SystemExit(5)

"""
watchdog - DECISAO do supervisor da migracao (logica pura, testavel sem processos reais).

O 345_migra_watchdog.py coleta as evidencias e chama `decidir()`. Regras (em ordem):

  1. runner VIVO:
     - batimento do motor recente (<= 15 min), ou motor entre lotes com log recente -> SAUDAVEL.
       Lentidao ou arquivo grande sem conclusao NAO sao travamento: o batimento vem de uma
       thread do proprio motor; se ela bate, o processo esta vivo e o rclone tem timeout proprio.
     - batimento parado > 20 min E log parado > 20 min E progresso parado > 30 min -> TRAVADO:
       encerra a cadeia da migracao (so ela) e reinicia pela tarefa (checkpoints no banco).
     - qualquer outra combinacao -> OBSERVAR (registra, nao age).
  2. runner MORTO:
     - fila concluida (nada retentavel nos lotes autorizados) -> CONCLUIDO (nao reinicia).
     - PAUSAR presente -> PAUSADO (respeita a pausa humana).
     - log com PARADA GLOBAL (401/cota/token) -> IMPEDIMENTO (nao reinicia: protege a conta).
     - disco abaixo da reserva -> IMPEDIMENTO.
     - limite de reinicios atingido (>= 4 em 24 h) -> IMPEDIMENTO (evita ciclo infinito).
     - ultimo reinicio ha < 20 min -> AGUARDAR (backoff).
     - ha itens retentaveis -> REINICIAR (pela tarefa existente, sem segunda instancia).
"""

from __future__ import annotations

from dataclasses import dataclass, field

LIMITE_REINICIOS_24H = 4
BACKOFF_REINICIO_MIN = 20
BATIMENTO_OK_MIN = 15
TRAVADO_BATIMENTO_MIN = 20
TRAVADO_LOG_MIN = 20
TRAVADO_PROGRESSO_MIN = 30


@dataclass
class Evidencias:
    runners_vivos: int                    # processos 339 vivos
    batimento_min: float | None           # idade da trava (None = sem trava: entre lotes ou parado)
    log_min: float | None                 # idade da ultima linha do log da fila
    progresso_min: float | None           # idade do ultimo progresso.json
    pausar: bool
    parada_global: bool
    disco_livre_gib: float
    reserva_gib: float
    retentaveis: int                      # itens dos lotes autorizados ainda nao VALIDADO e retentaveis
    reinicios_24h: int
    ultimo_reinicio_min: float | None
    detalhes: dict = field(default_factory=dict)


@dataclass
class Decisao:
    situacao: str          # SAUDAVEL | OBSERVAR | TRAVADO | CONCLUIDO | PAUSADO | IMPEDIMENTO | AGUARDAR | REINICIAR | DUPLICADO
    acao: str              # nenhuma | encerrar_e_reiniciar | reiniciar | registrar
    motivo: str
    humano: bool = False


def _velho(v: float | None, lim: float) -> bool:
    return v is None or v > lim


def decidir(e: Evidencias) -> Decisao:
    if e.runners_vivos > 1:
        return Decisao("DUPLICADO", "registrar", f"{e.runners_vivos} runners vivos: nao age; a trava do banco impede "
                                                 "copia dupla. Verificar.", humano=True)
    if e.runners_vivos == 1:
        if e.batimento_min is not None and e.batimento_min <= BATIMENTO_OK_MIN:
            return Decisao("SAUDAVEL", "nenhuma", f"batimento do motor ha {e.batimento_min:.1f} min")
        if e.batimento_min is None and e.log_min is not None and e.log_min <= BATIMENTO_OK_MIN:
            return Decisao("SAUDAVEL", "nenhuma", f"entre lotes; log atualizado ha {e.log_min:.1f} min")
        if (_velho(e.batimento_min, TRAVADO_BATIMENTO_MIN) and _velho(e.log_min, TRAVADO_LOG_MIN)
                and _velho(e.progresso_min, TRAVADO_PROGRESSO_MIN)):
            if e.reinicios_24h >= LIMITE_REINICIOS_24H:
                return Decisao("IMPEDIMENTO", "registrar", "travado, mas o limite de reinicios em 24 h foi atingido",
                               humano=True)
            return Decisao("TRAVADO", "encerrar_e_reiniciar",
                           f"sem batimento ({e.batimento_min}), log ({e.log_min}) e progresso ({e.progresso_min}) "
                           f"alem dos limites")
        return Decisao("OBSERVAR", "nenhuma", f"sinais mistos (batimento {e.batimento_min}, log {e.log_min}, "
                                              f"progresso {e.progresso_min}); nao age por uma checagem isolada")
    # runner morto
    if e.retentaveis == 0:
        return Decisao("CONCLUIDO", "nenhuma", "nenhum item retentavel nos lotes autorizados")
    if e.pausar:
        return Decisao("PAUSADO", "nenhuma", "arquivo PAUSAR presente (pausa humana respeitada)")
    if e.parada_global:
        return Decisao("IMPEDIMENTO", "registrar", "a fila parou por condicao global (credencial/cota); "
                                                    "nao reinicia para nao bloquear a conta", humano=True)
    if e.disco_livre_gib < e.reserva_gib:
        return Decisao("IMPEDIMENTO", "registrar", f"disco {e.disco_livre_gib:.1f} GiB abaixo da reserva "
                                                    f"{e.reserva_gib:.0f} GiB", humano=True)
    if e.reinicios_24h >= LIMITE_REINICIOS_24H:
        return Decisao("IMPEDIMENTO", "registrar", f"{e.reinicios_24h} reinicios em 24 h: problema recorrente",
                       humano=True)
    if e.ultimo_reinicio_min is not None and e.ultimo_reinicio_min < BACKOFF_REINICIO_MIN:
        return Decisao("AGUARDAR", "nenhuma", f"ultimo reinicio ha {e.ultimo_reinicio_min:.0f} min (backoff)")
    return Decisao("REINICIAR", "reiniciar", f"runner encerrado com {e.retentaveis} itens retentaveis")

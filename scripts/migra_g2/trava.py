"""
trava - exigencia de fase para os scripts que APAGAM no PARCEIRO (PD-18, autorizada pela Emmily em 04/11/2026).

Os scripts 380, 381 e 382 faziam DELETE so com --executar, sem consultar a `fase` do yaml (o 367, 377 e 379 consultam).
Agora todo --executar que apaga exige `fase: execucao`. Fica num modulo proprio para nao alterar a impressao digital
do codigo de envio (base.CODIGO_ESCRITA), que o 367 confere contra o ultimo teste do motor.
"""

from __future__ import annotations


class FaseNaoPermite(SystemExit):
    pass


def exigir_fase_execucao(c: dict, script: str) -> None:
    """Aborta (SystemExit) se a fase do yaml nao for 'execucao'. Chamar ANTES de abrir conexao de escrita."""
    fase = (c or {}).get("fase")
    if fase != "execucao":
        raise FaseNaoPermite(f"ABORTADO {script}: --executar apaga no PARCEIRO e exige fase 'execucao' "
                             f"(config/migracao_parceiro_g2.yaml esta em {fase!r}). Nada foi alterado.")

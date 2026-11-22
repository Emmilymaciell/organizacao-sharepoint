#!/usr/bin/env bash
# watchdog_copia.sh — roda a copia da RD102A ate terminar de verdade.
#
# A copia ja travou duas vezes hoje: as conexoes morrem (notebook dormindo,
# estrangulamento do Graph) e o rclone fica pendurado sem transferir nada,
# sem erro e sem sair. Este laco detecta isso e reinicia.
#
# Parada = a contagem de bytes transferidos nao muda por VIGIA_CICLOS
# verificacoes seguidas. Reiniciar e barato: o rclone pula o que ja subiu.
#
# Sai com 0 quando o rclone termina limpo. E ai que a notificacao dispara.

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

LISTA="${1:-output/rclone/rd102a_pequenos.txt}"
LOG="${2:-logs/rclone_rd102a.log}"
TRANSFERS="${3:-8}"
DESTINO="backup:RD102A_arquivo"
VIGIA_INTERVALO=120     # s entre verificacoes
VIGIA_CICLOS=${VIGIA_CICLOS:-4}   # x120s parado -> reinicia
MAX_TENTATIVAS=30

bytes_transferidos() {
  # ultima linha de stats -> "   11.355 GiB / 85.372 GiB, 13%, ..."
  grep -oE "^[^:]*:[^:]*:[^:]*INFO +: +[0-9.]+ [KMG]i?B / " "$LOG" 2>/dev/null \
    | tail -1 | grep -oE "[0-9.]+ [KMG]i?B" | head -1
}

for tentativa in $(seq 1 $MAX_TENTATIVAS); do
  echo "=== tentativa $tentativa — $(date '+%H:%M:%S') ==="

  bash scripts/rc.sh copy "prog:" "$DESTINO" \
      --files-from "$LISTA" \
      --transfers "$TRANSFERS" --checkers 8 \
      --drive-chunk-size 64M --onedrive-chunk-size 100M \
      --retries 20 --low-level-retries 20 --timeout 600s \
      --log-file "$LOG" --log-level INFO \
      --stats 1m --stats-one-line &
  RCPID=$!

  parado=0
  anterior=""
  while kill -0 "$RCPID" 2>/dev/null; do
    sleep $VIGIA_INTERVALO
    atual="$(bytes_transferidos)"
    if [ -n "$atual" ] && [ "$atual" = "$anterior" ]; then
      parado=$((parado + 1))
      echo "  [vigia] sem avanco ($parado/$VIGIA_CICLOS) — parado em $atual"
      if [ "$parado" -ge "$VIGIA_CICLOS" ]; then
        echo "  [vigia] TRAVOU. Matando e reiniciando."
        taskkill //F //IM rclone.exe >/dev/null 2>&1
        break
      fi
    else
      [ "$parado" -gt 0 ] && echo "  [vigia] voltou a andar"
      parado=0
    fi
    anterior="$atual"
  done

  wait "$RCPID" 2>/dev/null
  codigo=$?

  if [ "$codigo" -eq 0 ] && [ "$parado" -lt "$VIGIA_CICLOS" ]; then
    echo "=== COPIA CONCLUIDA — $(date '+%H:%M:%S') ==="
    grep -c "Copied (new)" "$LOG" 2>/dev/null | sed 's/^/copiados nesta rodada: /'
    exit 0
  fi

  echo "  saiu com codigo $codigo — reiniciando em 30s"
  sleep 30
done

echo "=== DESISTIU apos $MAX_TENTATIVAS tentativas ==="
exit 1

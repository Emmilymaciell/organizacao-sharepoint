#!/usr/bin/env bash
# rc.sh — chama o rclone com o remote "prog:" (SharePoint) definido por
# variaveis de ambiente, lidas do .env no momento da execucao.
#
# Por que assim: o rclone aceita definir um remote inteiro por env var
# (RCLONE_CONFIG_<NOME>_<CHAVE>). Com isso o segredo NAO e gravado no
# rclone.conf nem aparece na linha de comando — continua existindo so no
# .env, como manda a Regra n3 do projeto.
#
# Uso:  scripts/rc.sh lsd prog:
#       scripts/rc.sh copy prog: backup:RD102A_arquivo --files-from ...

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$RAIZ/.env"

[ -f "$ENV_FILE" ] || { echo "ERRO: $ENV_FILE nao encontrado" >&2; exit 1; }

# le o .env sem ecoar nada
le() {
  local chave="$1" linha
  linha="$(grep -m1 -E "^[[:space:]]*${chave}[[:space:]]*=" "$ENV_FILE" || true)"
  [ -n "$linha" ] || { echo "ERRO: $chave ausente no .env" >&2; exit 1; }
  printf '%s' "${linha#*=}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

RCLONE="${RCLONE:-/c/Users/$USERNAME/AppData/Local/Microsoft/WinGet/Packages/Rclone.Rclone_Microsoft.Winget.Source_8wekyb3d8bbwe/rclone-v1.75.1-windows-amd64/rclone.exe}"
[ -x "$RCLONE" ] || RCLONE="$(command -v rclone)"

# biblioteca "Programa" do site https://contoso.sharepoint.com/sites/exemplo
DRIVE_PROG="00000000-0000-0000-0000-000000000000"

export RCLONE_CONFIG_PROG_TYPE="onedrive"
export RCLONE_CONFIG_PROG_CLIENT_ID="$(le GRAPH_CLIENT_ID)"
export RCLONE_CONFIG_PROG_CLIENT_SECRET="$(le GRAPH_CLIENT_SECRET)"
export RCLONE_CONFIG_PROG_CLIENT_CREDENTIALS="true"
export RCLONE_CONFIG_PROG_TENANT="$(le GRAPH_TENANT_ID)"
export RCLONE_CONFIG_PROG_DRIVE_ID="$DRIVE_PROG"
export RCLONE_CONFIG_PROG_DRIVE_TYPE="documentLibrary"

exec "$RCLONE" "$@"

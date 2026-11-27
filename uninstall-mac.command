#!/bin/bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
BIN="$(uv tool dir --bin)/hsu-reserve-mcp"
"$BIN" uninstall
uv tool uninstall hsu-reserve
printf '\n제거되었습니다.\n'
read -r -p '엔터를 누르면 닫힙니다...'

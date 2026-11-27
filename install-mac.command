#!/bin/bash
# Download and extract the repository ZIP, then open this file.
set -euo pipefail
cd "$(dirname "$0")"
if ! /bin/sh ./install.sh; then
  printf '\n설치에 실패했습니다. 위 오류를 확인하세요.\n'
  read -r -p '엔터를 누르면 닫힙니다...'
  exit 1
fi
printf '\n'
read -r -p '엔터를 누르면 닫힙니다...'

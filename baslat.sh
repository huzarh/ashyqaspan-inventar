#!/usr/bin/env sh
set -eu
cd -- "$(dirname -- "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.8 veya daha yenisi gerekiyor. Önce Python 3 kurun."
  exit 1
fi
exec python3 app.py "$@"

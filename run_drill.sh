#!/usr/bin/env bash
# Convenience wrapper: load .env, keep the drill env in sync, then run the script.
#
#   ./run_drill.sh              # full drill
#   ./run_drill.sh --dry-run    # print commands only
#   ./run_drill.sh --force-fail clean_redis
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "missing .env - run: cp .env.example .env" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

mkdir -p run/alerts reports

if [[ ! -L frontend/current ]]; then
  ln -sfn releases/v1 frontend/current   # relative: must resolve inside the web container
fi

exec python3 scripts/drill_rollback.py "$@"

#!/usr/bin/env bash
# AC4 evidence: prove --dry-run changes no container state.
set -euo pipefail
cd "$(dirname "$0")/.."

[[ -f .env ]] || { echo "missing .env" >&2; exit 2; }
set -a; source .env; set +a
mkdir -p reports

snapshot() {
  docker ps -q | xargs -r docker inspect \
    --format '{{.Name}} {{.Image}} {{.State.StartedAt}}' | sort
}

snapshot > /tmp/drill_ps_before.txt
./run_drill.sh --dry-run > reports/dry_run_console.txt 2>&1
echo "dry-run exit=$?"
snapshot > /tmp/drill_ps_after.txt

if diff -u /tmp/drill_ps_before.txt /tmp/drill_ps_after.txt > reports/dry_run_docker_ps_diff.txt; then
  {
    echo "# no state change: container name / image / StartedAt identical before and after"
    echo "# containers compared: $(wc -l < /tmp/drill_ps_before.txt)"
  } >> reports/dry_run_docker_ps_diff.txt
  echo "PASS: dry-run changed nothing"
else
  echo "FAIL: container state changed" >&2
  exit 1
fi

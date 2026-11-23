#!/usr/bin/env bash
# AC4 evidence: prove --dry-run changes no container state.
#
# Produces (all committed, so a reviewer can read them without running anything):
#   reports/dry_run_docker_ps_before.txt      raw `docker ps -a` before the dry-run
#   reports/dry_run_docker_ps_after.txt       raw `docker ps -a` after  the dry-run
#   reports/dry_run_compose_ps_before.txt     project-scoped `docker compose ps` before
#   reports/dry_run_compose_ps_after.txt      project-scoped `docker compose ps` after
#   reports/dry_run_fingerprint_before.txt    Id / Name / Image / StartedAt, sorted
#   reports/dry_run_fingerprint_after.txt     same, after
#   reports/dry_run_console.txt               full console output of the dry-run
#   reports/dry_run_diff.txt                  unified diff of the two fingerprint files
#   reports/dry_run_verdict.txt               machine-checkable PASS/FAIL summary
#
# Compared fields: container Id, Name, Image, State.StartedAt.
# `Status` ("Up 3 minutes") is captured in the raw `docker ps` files but excluded
# from the diff on purpose: it drifts with wall-clock time and would make the diff
# non-empty even when nothing was touched. A different Id means the container was
# recreated; a different StartedAt means it was restarted.
set -euo pipefail
cd "$(dirname "$0")/.."

[[ -f .env ]] || { echo "missing .env (cp .env.example .env)" >&2; exit 2; }
set -a; source .env; set +a
mkdir -p reports

fingerprint() {
  docker ps -q | xargs -r docker inspect \
    --format '{{.Id}} {{.Name}} {{.Image}} {{.State.StartedAt}}' | sort
}

docker ps -a --format 'table {{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}' \
  > reports/dry_run_docker_ps_before.txt
docker compose -p "${COMPOSE_PROJECT_NAME:-rollback-drill}" ps \
  > reports/dry_run_compose_ps_before.txt
fingerprint > reports/dry_run_fingerprint_before.txt

set +e
./run_drill.sh --dry-run > reports/dry_run_console.txt 2>&1
dry_rc=$?
set -e
echo "dry-run exit=${dry_rc}" | tee -a reports/dry_run_console.txt

docker ps -a --format 'table {{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}' \
  > reports/dry_run_docker_ps_after.txt
docker compose -p "${COMPOSE_PROJECT_NAME:-rollback-drill}" ps \
  > reports/dry_run_compose_ps_after.txt
fingerprint > reports/dry_run_fingerprint_after.txt

before_lines=$(wc -l < reports/dry_run_fingerprint_before.txt)
after_lines=$(wc -l < reports/dry_run_fingerprint_after.txt)

if diff -u reports/dry_run_fingerprint_before.txt reports/dry_run_fingerprint_after.txt \
    > reports/dry_run_diff.txt; then
  diff_state="identical"
else
  diff_state="changed"
fi

{
  echo "dry_run_exit=${dry_rc}"
  echo "containers_before=${before_lines}"
  echo "containers_after=${after_lines}"
  echo "fingerprint_diff=${diff_state}"
  echo "compared_fields=Id,Name,Image,State.StartedAt"
  if [[ "${dry_rc}" -ne 0 || "${diff_state}" != "identical" || "${before_lines}" != "${after_lines}" ]]; then
    echo "verdict=FAIL"
  elif [[ "${before_lines}" -eq 0 ]]; then
    echo "verdict=FAIL (no containers matched: drill env was down, comparison is meaningless)"
  else
    echo "verdict=PASS"
  fi
} | tee reports/dry_run_verdict.txt

grep -q '^verdict=PASS' reports/dry_run_verdict.txt

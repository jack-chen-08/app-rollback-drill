#!/usr/bin/env bash
# Bring the drill environment up from scratch (idempotent).
set -euo pipefail
cd "$(dirname "$0")/.."   # project root (this script lives in scripts/)

[[ -f .env ]] || { echo "missing .env - run: cp .env.example .env" >&2; exit 2; }
set -a; source .env; set +a

mkdir -p run/alerts reports
ln -sfn releases/v1 frontend/current   # relative: must resolve inside the web container

echo "== build images =="
docker build -q -t "${API_IMAGE}:${GOOD_TAG}" --build-arg APP_VERSION="${GOOD_TAG}" \
  --build-arg HEALTH_FORCE_STATUS=200 app
docker build -q -t "${API_IMAGE}:${BAD_TAG}" --build-arg APP_VERSION="${BAD_TAG}" \
  --build-arg HEALTH_FORCE_STATUS=500 app

echo "== start infra =="
API_TAG="${GOOD_TAG}" docker compose up -d db redis web watchdog

echo "== start api (${GOOD_TAG}) =="
API_TAG="${GOOD_TAG}" docker compose up -d api

echo "== wait for health =="
for _ in $(seq 1 60); do
  if curl -fsS "${API_BASE_URL}/health" >/dev/null 2>&1; then
    echo "api healthy at ${API_BASE_URL}"
    docker compose ps
    exit 0
  fi
  sleep 2
done

echo "api did not become healthy" >&2
docker compose logs --tail 40 api >&2
exit 1

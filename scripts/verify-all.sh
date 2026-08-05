#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
SKIP_E2E="${SKIP_E2E:-0}"
SKIP_DOCKER_BUILD="${SKIP_DOCKER_BUILD:-0}"

run_step() {
  printf '\n==> %s\n' "$1"
  shift
  "$@"
}

cd "$ROOT_DIR"
run_step "Frontend unit tests" sh -c "cd frontend && npm test"
run_step "Frontend build" sh -c "cd frontend && npm run build"
if [ "$SKIP_DOCKER_BUILD" != "1" ]; then
  run_step "Docker build" docker compose build
fi
run_step "Docker startup" docker compose up -d
run_step "Docker status" docker compose ps
run_step "Backend dependency check" docker compose exec -T backend python -m pip check
run_step "Backend tests" docker compose exec -T backend python -m pytest --disable-warnings --tb=short
run_step "Redis connectivity" docker compose exec -T redis redis-cli ping
run_step "Liveness" curl -fsS http://127.0.0.1:8000/livez
run_step "Readiness" curl -fsS http://127.0.0.1:8000/readyz
run_step "API documentation" curl -fsS -o /dev/null http://127.0.0.1:8000/docs
if [ "$SKIP_E2E" != "1" ]; then
  run_step "Playwright E2E" sh -c "cd frontend && npm run test:e2e"
fi

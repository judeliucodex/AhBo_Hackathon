#!/bin/sh
# Real-app smoke for the operator console (GLM task 03) — host-side orchestration only.
#
# Creates a PRIVATE docker network, runs a throwaway `ahbo-api` (fake mode) with GENERATED secrets
# (a temp env file; the real .env is never read), then runs the pytest smoke test in the `ahbo-browser`
# image and tears everything down. Screenshots land in docs/screens/operator/.
set -eu

ROOT=$(cd "$(dirname "$0")/../../.." && pwd)          # repo root (/opt/ahbo)
NET=ahbo-t-glm03
API_C=ahbo-t-glm03-api
PW='smoke-op-glm03'

cleanup() {
  docker rm -f "$API_C" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  [ -n "${ENVF:-}" ] && rm -f "$ENVF" || true
}
trap cleanup EXIT INT TERM

echo "== generating throwaway secrets (never printed) =="
APIRUN="docker run --rm -v $ROOT:/repo --workdir /repo/brain ahbo-api"
HASH=$($APIRUN python -c "from ahbo.api.auth import hash_password; print(hash_password('$PW'))")
ENVF=$(mktemp)
{
  printf 'APP_SECRET=%s\n'     "$($APIRUN python -c 'import secrets; print(secrets.token_hex(32))')"
  printf 'SESSION_SECRET=%s\n' "$($APIRUN python -c 'import secrets; print(secrets.token_hex(32))')"
  printf 'INTERNAL_TOKEN=%s\n' "$($APIRUN python -c 'import secrets; print(secrets.token_hex(24))')"
  printf 'OPERATOR_PASSWORD_HASH=%s\n' "$HASH"
  printf 'AHBO_FAKE=all\nAHBO_PROFILE=demo\n'
  printf 'PUBLIC_BASE_URL=http://api:8000\n'
  printf 'AHBO_DATA_DIR=/tmp/ahbo-smoke-data\n'
} > "$ENVF"
chmod 600 "$ENVF"

docker network create "$NET" >/dev/null 2>&1 || true   # reuse if left over from an earlier run

echo "== starting throwaway ahbo-api on network $NET =="
docker run -d --name "$API_C" --network "$NET" --network-alias api \
  --env-file "$ENVF" -v "$ROOT:/repo" ahbo-api >/dev/null

echo "== waiting for /healthz =="
i=0
while [ "$i" -lt 30 ]; do
  if docker exec "$API_C" python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=2)" >/dev/null 2>&1; then
    break
  fi
  i=$((i + 1)); sleep 1
done
[ "$i" -lt 30 ] || { echo "api did not come up"; docker logs --tail 40 "$API_C"; exit 1; }

echo "== running the smoke test in ahbo-browser =="
docker run --rm --network "$NET" -v "$ROOT:/repo" --workdir /repo/brain \
  -e AHBO_SMOKE_BASE_URL=http://api:8000 -e AHBO_SMOKE_PASSWORD="$PW" \
  ahbo-browser sh -c "pip install --break-system-packages -q pytest==9.2.0 && \
    python -m pytest -q tests/operator_ui/test_smoke_real_app.py --confcutdir=/repo/brain/tests/operator_ui"

echo "== done; screenshots in docs/screens/operator/ =="

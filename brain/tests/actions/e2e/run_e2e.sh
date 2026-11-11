#!/usr/bin/env bash
# Real-Playwright integration topology for the Actions runner (task 02 step 7, integration test).
#
# Creates the private network `ahbo-t-actions`, starts the `ahbo-browser` container as a Playwright
# server (`playwright run-server --port 3000 --host 0.0.0.0`) and an `ahbo-api` container serving the
# mock-centre API + static page, then runs tests/actions/test_playwright_integration.py inside the api
# container (the runner connects out to ws://browser:3000/ and the browser fetches http://api:8000/...).
# Removes the containers and the network afterwards. Run from the host, from /opt/ahbo:
#
#   bash brain/tests/actions/e2e/run_e2e.sh
#
set -uo pipefail
cd /opt/ahbo

NET=ahbo-t-actions
BR=ahbo-t2-browser
API=ahbo-t2-api

cleanup() {
  docker rm -f "$BR" "$API" >/dev/null 2>&1
  docker network rm "$NET" >/dev/null 2>&1
}
trap cleanup EXIT

docker network create "$NET" >/dev/null
docker run -d --name "$BR" --network "$NET" --network-alias browser \
  --entrypoint bash ahbo-browser -c "playwright run-server --port 3000 --host 0.0.0.0" >/dev/null
docker run -d --name "$API" --network "$NET" --network-alias api -v /opt/ahbo:/repo \
  -e AHBO_FAKE=all -e AHBO_PROFILE=demo -e PUBLIC_BASE_URL=https://demo.invalid \
  -e AHBO_ACTION_INTERNAL_BASE=http://api:8000 -e AHBO_BROWSER_WS=ws://browser:3000/ \
  --entrypoint bash ahbo-api -c "python /repo/brain/tests/actions/e2e/mock_app.py" >/dev/null

# wait until the mock site answers inside the network
for i in $(seq 1 30); do
  if docker exec "$API" python -c \
    "import urllib.request;urllib.request.urlopen('http://api:8000/mock-centre/',timeout=2)" 2>/dev/null; then
    break
  fi
  sleep 1
  [ "$i" = 30 ] && { echo "mock site never came up"; docker logs "$API" | tail -20; exit 1; }
done
echo "mock site up; running the integration tests"
docker exec -e AHBO_ACTION_E2E=1 -w /repo/brain "$API" python -m pytest -q tests/actions/test_playwright_integration.py

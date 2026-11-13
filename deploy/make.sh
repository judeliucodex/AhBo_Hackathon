#!/bin/sh
# Usage: deploy/make.sh <target> [VAR=value ...]   — same as `make <target>`, run inside a small container
# e.g. deploy/make.sh eval EVAL_ARGS="--suite scenarios"   ·   deploy/make.sh screens   ·   deploy/make.sh doctor
# (the host has Docker but no `make`; nothing is installed on the host). The repo is mounted at the same
# absolute path so Compose's relative bind mounts resolve to host paths.
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
if command -v make >/dev/null 2>&1; then exec make -C "$REPO" "$@"; fi
docker image inspect ahbo-make:latest >/dev/null 2>&1 || \
  docker build -q -t ahbo-make:latest -f "$REPO/deploy/Dockerfile.make" "$REPO/deploy" >/dev/null
TTY=""; [ -t 0 ] && [ -t 1 ] && TTY="-it"
exec docker run --rm $TTY -v /var/run/docker.sock:/var/run/docker.sock -v "$REPO:$REPO" -w "$REPO" \
  -e HOST="${HOST:-https://srv2135273.hstgr.cloud}" ahbo-make:latest "$@"

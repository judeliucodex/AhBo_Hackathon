# Runs the repo Makefile without installing anything on the host (the VPS has no `make`).
# Used by deploy/make.sh; the docker socket is mounted so targets drive the host's Docker.
FROM docker:29.9.3-cli
RUN apk add --no-cache make bash
WORKDIR /opt/ahbo
ENTRYPOINT ["make"]

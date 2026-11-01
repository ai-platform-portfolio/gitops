# syntax=docker/dockerfile:1.7

FROM --platform=$BUILDPLATFORM golang:1.26.9@sha256:f1f0bcc2c524a3ced375fcb4d1ecb7aa371aa7070e112599aaca45cc02d0101b AS build

WORKDIR /src

# Argo v3.5.5, commit a5038b0. Both source and runtime are immutable.
ADD --checksum=sha256:76da604d02e27ed7a616d7c88f66a3c87554f9c2f274b62f2513477ff89b40d1 https://api.github.com/repos/argoproj/argo-cd/tarball/a5038b0e2bbac1715754380ae0084df8e3d5b48c /tmp/argocd.tar.gz
RUN tar -xzf /tmp/argocd.tar.gz --strip-components=1 && rm /tmp/argocd.tar.gz

COPY delete-hook-race.patch /tmp/delete-hook-race.patch
RUN git apply --check /tmp/delete-hook-race.patch && git apply /tmp/delete-hook-race.patch

ENV GOMAXPROCS=2 CGO_ENABLED=0

# Includes upstream's AlreadyExists ownership, health and retry regressions.
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    go test -p 2 ./controller -run '^TestFinalizeAppDeletion' -count=1

ARG TARGETOS
ARG TARGETARCH
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    make argocd-all GOOS=${TARGETOS} GOARCH=${TARGETARCH} CGO_FLAG=0 \
      GOFLAGS='-p=2 -trimpath' VERSION=3.5.5-ai116 \
      GIT_COMMIT=a5038b0e2bbac1715754380ae0084df8e3d5b48c \
      GIT_TAG=v3.5.5-ai116 GIT_TREE_STATE=dirty

FROM quay.io/argoproj/argocd:v3.5.5@sha256:13db7b10db23283b1b36f855046602e18539a8a8204c9f6f039e8896c8897518

LABEL org.opencontainers.image.version="v3.5.5-ai116" \
      org.opencontainers.image.description="Argo CD 3.5.5 with the reviewed delete-hook race backport from argoproj/argo-cd#29687"

COPY --from=build --chown=0:0 --chmod=0555 /src/dist/argocd /usr/local/bin/argocd

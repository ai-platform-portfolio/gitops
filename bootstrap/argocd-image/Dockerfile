# syntax=docker/dockerfile:1.7

FROM --platform=$BUILDPLATFORM golang:1.26.10@sha256:cfa27959837144bf4649802ba38e59daffd0dd2a7994aa83daaf113285b0f551 AS build

WORKDIR /src

# Argo v3.5.9, commit 70204ad. Both source and runtime are immutable.
ADD --checksum=sha256:eb540eaf21baa9fee6e503b0a61e5af4bb7dabfdcfa41f7d824d708c8bfbedb9 https://api.github.com/repos/argoproj/argo-cd/tarball/70204ad7508c42758f196a6fdbf81e6e798ad51a /tmp/argocd.tar.gz
RUN tar -xzf /tmp/argocd.tar.gz --strip-components=1 && rm /tmp/argocd.tar.gz

# Adapt upstream #29886 to this pinned cache layout; the release includes the delete-hook and CRD refresh fixes.
COPY watch-retry-backoff.patch /tmp/watch-retry-backoff.patch
RUN git apply --check /tmp/watch-retry-backoff.patch && git apply /tmp/watch-retry-backoff.patch

ENV GOMAXPROCS=2 CGO_ENABLED=0

# Fix the scanned HTTP/HTML dependency findings before tests and compilation.
RUN --mount=type=cache,target=/go/pkg/mod \
    go get golang.org/x/net@v0.62.0 && go mod tidy

# Includes upstream's AlreadyExists ownership, health and retry regressions.
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    go test -p 2 ./controller -run '^Test(FinalizeAppDeletion.*|HandleManagedCRDUpdated)$' -count=1 && \
    go test -p 2 ./controller/cache -run '^Test(ManagedCRDIndexFunc|AppsToNotify|HandleResourceUpdated)$' -count=1 && \
    go test -p 2 github.com/argoproj/argo-cd/gitops-engine/pkg/cache -count=1

ARG TARGETOS
ARG TARGETARCH
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    make argocd-all GOOS=${TARGETOS} GOARCH=${TARGETARCH} CGO_FLAG=0 \
      GOFLAGS='-p=2 -trimpath' VERSION=3.5.9-ai292 \
      GIT_COMMIT=70204ad7508c42758f196a6fdbf81e6e798ad51a \
      GIT_TAG=v3.5.9-ai292 GIT_TREE_STATE=dirty

FROM quay.io/argoproj/argocd:v3.5.9@sha256:3b7416d6a448f3cb9c445fb72f90b4f76be204f4541cd146115e53c8534a8d5a

LABEL org.opencontainers.image.version="v3.5.9-ai292" \
      org.opencontainers.image.description="Argo CD 3.5.9 with watch-retry backoff (#29886)"

# This image is used only by the application controller. Manifest rendering and
# Git checkout tools stay in the separate upstream repo-server image.
USER root
RUN rm -f /usr/bin/pebble /usr/local/bin/git-lfs /usr/local/bin/helm /usr/local/bin/kustomize

COPY --from=build --chown=0:0 --chmod=0555 /src/dist/argocd /usr/local/bin/argocd

USER $ARGOCD_USER_ID
RUN argocd version --client && \
    test -x /usr/local/bin/argocd-application-controller && \
    test -x /usr/local/bin/argocd-k8s-auth && test -x /usr/bin/tini

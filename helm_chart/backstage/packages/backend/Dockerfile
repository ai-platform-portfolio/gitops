# Host build: Node 24, yarn install --immutable, yarn tsc, yarn build:backend.
FROM node:24.22.1-trixie-slim@sha256:43b9ba1142b8dcf33fa8f922ca1be85714adc444e0586c7cf7726daaeabc1bea

ENV NODE_ENV=production \
    SCARF_ANALYTICS=false \
    YARN_ENABLE_TELEMETRY=0

RUN corepack enable
USER node
WORKDIR /app

COPY --chown=node:node .yarnrc.yml backstage.json package.json yarn.lock ./
COPY --chown=node:node packages/backend/dist/skeleton.tar.gz ./
RUN tar xzf skeleton.tar.gz && rm skeleton.tar.gz
RUN --mount=type=cache,target=/home/node/.yarn,uid=1000,gid=1000 \
    yarn workspaces focus --all --production

COPY --chown=node:node packages/backend/dist/bundle.tar.gz app-config.yaml ./
RUN tar xzf bundle.tar.gz && rm bundle.tar.gz
COPY --chown=node:node catalog ./catalog
COPY --chown=node:node templates ./templates
COPY --chown=node:node techdocs ./techdocs

EXPOSE 7007
CMD ["node", "packages/backend", "--config", "app-config.yaml", "--config", "/etc/backstage/integrations/app-config.integrations.yaml"]

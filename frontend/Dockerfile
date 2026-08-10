# syntax=docker/dockerfile:1.7

ARG NODE_BASE_IMAGE=node:slim
ARG NGINX_BASE_IMAGE=nginxinc/nginx-unprivileged:latest

FROM ${NODE_BASE_IMAGE} AS build
WORKDIR /app

ENV CI=true \
    npm_config_registry=https://registry.npmjs.org/ \
    npm_config_audit=false \
    npm_config_fund=false \
    npm_config_update_notifier=false \
    npm_config_fetch_retries=2 \
    npm_config_fetch_retry_mintimeout=5000 \
    npm_config_fetch_retry_maxtimeout=20000 \
    npm_config_fetch_timeout=60000

# Copy only dependency metadata first so Docker can cache installs.
# package-lock.json is intentionally not copied/generated outside Docker here:
# the previous generated lockfile pointed to an internal sandbox registry and
# caused npm to retry indefinitely on user machines.
COPY package.json .npmrc ./

# BuildKit cache speeds up repeated installs, but this still performs a full
# in-Docker React dependency install from the public npm registry.
RUN --mount=type=cache,target=/root/.npm \
    npm install --no-audit --no-fund --loglevel=notice

COPY index.html vite.config.js ./
COPY src ./src

ARG VITE_API_BASE_URL=""
ARG VITE_WS_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_WS_BASE_URL=${VITE_WS_BASE_URL}

RUN npm run build

FROM ${NGINX_BASE_IMAGE} AS runtime

ENV BACKEND_URL=http://backend:8000 \
    SERVER_NAME=_ \
    CLIENT_MAX_BODY_SIZE=1g \
    PROXY_READ_TIMEOUT=300s \
    PROXY_SEND_TIMEOUT=300s

COPY docker/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/ >/dev/null || exit 1

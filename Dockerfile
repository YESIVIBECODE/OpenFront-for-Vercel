# Use an official Node runtime as the base image
FROM node:24-slim AS base
WORKDIR /usr/src/app
RUN npm install --global --ignore-scripts npm@12.1.0

# Build stage - install ALL dependencies and build
FROM base AS build
ENV HUSKY=0
# Build the browser bundle for a private, self-hosted game server. In this
# mode the client uses this deployment's lobby list and local player IDs.
ARG OPENFRONT_SELF_HOSTED=true
ENV OPENFRONT_SELF_HOSTED=${OPENFRONT_SELF_HOSTED}
# Copy package files first for better caching. The workspace manifests come
# along so npm ci can link node_modules/@openfront/* to packages/*.
COPY package*.json .npmrc ./
COPY packages/engine/package.json ./packages/engine/
COPY packages/engine-api/package.json ./packages/engine-api/
COPY packages/engine-lib/package.json ./packages/engine-lib/
COPY packages/shared/package.json ./packages/shared/
COPY packages/zbin/package.json ./packages/zbin/
RUN --mount=type=cache,target=/root/.npm \
    npm ci --ignore-scripts

# Copy only what's needed for build
COPY tsconfig.json tsconfig.base.json ./
COPY vite.config.ts ./
COPY index.html ./
COPY client-api.json ./
COPY resources ./resources
COPY proprietary ./proprietary
COPY src ./src
COPY packages ./packages
# build-prod runs scripts/buildAssetHashes.ts after vite, to emit
# static/asset-hashes.json and static/core-version.txt for the desktop
# release descriptor. Without this the image build fails at that step with
# ERR_MODULE_NOT_FOUND -- the unit suite cannot catch it, because only the
# container build runs build-prod from a copied tree.
COPY scripts ./scripts

ARG GIT_COMMIT=unknown
ENV GIT_COMMIT="$GIT_COMMIT"
RUN npm run build-prod

# Production dependencies stage - separate from build
FROM base AS prod-deps
ENV HUSKY=0
ENV NPM_CONFIG_IGNORE_SCRIPTS=1
COPY package*.json .npmrc ./
COPY packages/engine/package.json ./packages/engine/
COPY packages/engine-api/package.json ./packages/engine-api/
COPY packages/engine-lib/package.json ./packages/engine-lib/
COPY packages/shared/package.json ./packages/shared/
COPY packages/zbin/package.json ./packages/zbin/
RUN --mount=type=cache,target=/root/.npm \
    npm ci --omit=dev --ignore-scripts

# Final production image
FROM base

# Install system dependencies
RUN apt-get update && apt-get install -y \
    nginx \
    curl \
    wget \
    supervisor \
    apache2-utils \
    && rm -rf /var/lib/apt/lists/*

# Update worker_connections in nginx.conf
RUN sed -i 's/worker_connections [0-9]*/worker_connections 8192/' /etc/nginx/nginx.conf

# Setup supervisor configuration
RUN mkdir -p /var/log/supervisor
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Copy Nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf
RUN rm -f /etc/nginx/sites-enabled/default

# Script that generates the create-game worker upstream at container start.
COPY generate-nginx-upstream.sh /usr/local/bin/generate-nginx-upstream.sh
RUN chmod +x /usr/local/bin/generate-nginx-upstream.sh

# Copy production node_modules from prod-deps stage (cached separately from build)
COPY --from=prod-deps /usr/src/app/node_modules ./node_modules
COPY package*.json ./

# Copy built artifacts from build stage
COPY --from=build /usr/src/app/static ./static

COPY resources ./resources

# Remove maps because they are not used by the server.
RUN rm -rf ./resources/maps
COPY tsconfig.json tsconfig.base.json ./
COPY client-api.json ./
COPY src ./src
# node_modules/@openfront/* are symlinks into packages/; without this copy
# they dangle and the server dies at boot.
COPY packages ./packages


ARG GIT_COMMIT=unknown
RUN echo "$GIT_COMMIT" > static/commit.txt

ENV GIT_COMMIT="$GIT_COMMIT"
ENV OPENFRONT_SELF_HOSTED=true
# Defaults for a single self-hosted server. Railway supplies PORT and
# RAILWAY_PUBLIC_DOMAIN at runtime; the same image also runs locally in Docker.
ENV GAME_ENV=dev \
    DOMAIN=localhost \
    TURNSTILE_SITE_KEY=1x00000000000000000000AA \
    NUM_WORKERS=1 \
    GIT_COMMIT=SELF_HOSTED

RUN <<'EOF' tee /usr/local/bin/start.sh
#!/bin/sh
# Generate the create-game nginx upstream from NUM_WORKERS before nginx starts.
/usr/local/bin/generate-nginx-upstream.sh

# Railway assigns the public hostname after the service gets a domain. Pin it
# into the page bootstrap so browsers connect back to this server for sockets.
if [ -z "${GAME_HOST:-}" ] && [ -n "${RAILWAY_PUBLIC_DOMAIN:-}" ]; then
    GAME_HOST="$RAILWAY_PUBLIC_DOMAIN"
    export GAME_HOST
fi

# Railway routes HTTP and WebSocket traffic to PORT. Nginx is the public
# entrypoint; Node workers stay on their internal 3000+ ports.
port="${PORT:-80}"
case "$port" in
    "" | *[!0-9]*)
        echo "PORT must be numeric, got '$port'" >&2
        exit 1
        ;;
esac
sed "s/listen 80 default_server;/listen ${port} default_server;/" \
    /etc/nginx/conf.d/default.conf > /tmp/default.conf
mv /tmp/default.conf /etc/nginx/conf.d/default.conf

if [ "$DOMAIN" = openfront.dev ] && [ "$SUBDOMAIN" != main ]; then
    exec timeout 25h /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
else
    exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
fi
EOF
RUN chmod +x /usr/local/bin/start.sh
ENTRYPOINT ["/usr/local/bin/start.sh"]

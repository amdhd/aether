# syntax=docker/dockerfile:1

# --- Builder: produce the static SPA bundle ---
# Base images are pinned by digest as well as tag (Dependabot proposes bumps).
FROM node:24.22.1-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53 AS builder

WORKDIR /app

# Fail the build if its Node runtime predates the October 2026 security fixes.
RUN node -e 'const [major, minor, patch] = process.versions.node.split(".").map(Number); console.log(`Frontend build Node.js: ${process.versions.node}`); if (major !== 24 || minor < 22 || (minor === 22 && patch < 1)) process.exit(1)'

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
# VITE_API_URL is baked in at build time; default to a same-origin /api path so
# the image works behind the CloudFront/ALB unified-origin setup without a rebuild.
ARG VITE_API_URL=/api
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build


# --- Runtime: serve the static bundle via nginx ---
# NOTE: the primary production path serves web/dist from S3 + CloudFront (see
# infra/terraform). This image is the fallback for running the frontend as a
# container (e.g. ECS/local) — it is NOT the Vite dev server.
#
# nginx-unprivileged runs the master process as uid 101 rather than root, which
# is why it listens on 8080: binding below 1024 needs root.
FROM nginxinc/nginx-unprivileged:1.30.6-alpine@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03 AS runtime

# SPA history fallback: unknown routes return index.html so client-side routing
# works on refresh/deep-link. Written as root, then back to the image's user.
#
# `apk upgrade` applies Alpine security fixes published since the base image
# was built (musl, zlib, expat, … — the same reason the API image runs
# `apt-get upgrade`), so the Trivy gate tracks upstream fixes rather than the
# age of the base tag.
USER root
RUN apk upgrade --no-cache \
    && printf 'server {\n\
    listen 8080;\n\
    server_name _;\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
    location / {\n\
        try_files $uri $uri/ /index.html;\n\
    }\n\
}\n' > /etc/nginx/conf.d/default.conf
USER 101

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://localhost:8080/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]

# syntax=docker/dockerfile:1

# --- Builder: produce the static SPA bundle ---
FROM node:22-alpine AS builder

WORKDIR /app

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
FROM nginx:1.27-alpine AS runtime

# SPA history fallback: unknown routes return index.html so client-side routing
# works on refresh/deep-link.
RUN printf 'server {\n\
    listen 80;\n\
    server_name _;\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
    location / {\n\
        try_files $uri $uri/ /index.html;\n\
    }\n\
}\n' > /etc/nginx/conf.d/default.conf

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://localhost:80/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]

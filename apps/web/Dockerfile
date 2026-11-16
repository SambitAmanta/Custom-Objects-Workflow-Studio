# Multi-stage production Dockerfile for Forge Web Studio
FROM node:20-alpine AS builder

WORKDIR /app

# Copy root workspace manifests
COPY package*.json ./
COPY packages/schema-renderer/package*.json ./packages/schema-renderer/
COPY apps/web/package*.json ./apps/web/

# Install dependencies across monorepo
RUN npm ci

# Copy full source tree
COPY packages/schema-renderer ./packages/schema-renderer
COPY apps/web ./apps/web

# Build schema renderer and web app
RUN npm run build --workspace=@forge/schema-renderer
RUN npm run build --workspace=forge-web-studio

# Nginx Alpine runtime for static SPA serving
FROM nginx:alpine

COPY --from=builder /app/apps/web/dist /usr/share/nginx/html

# Custom Nginx configuration for client-side React routing
RUN echo 'server {' \
    '    listen 80;' \
    '    server_name _;' \
    '    root /usr/share/nginx/html;' \
    '    index index.html;' \
    '    location / {' \
    '        try_files $uri $uri/ /index.html;' \
    '    }' \
    '}' > /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]

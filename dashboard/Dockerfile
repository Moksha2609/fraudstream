# ---- build -------------------------------------------------------------
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# ---- serve -------------------------------------------------------------
FROM nginx:1.27-alpine
# apache2-utils provides htpasswd for the optional basic-auth entrypoint hook
RUN apk add --no-cache apache2-utils
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY auth.conf /etc/nginx/auth.conf
COPY docker-entrypoint.d/10-dashboard-auth.sh /docker-entrypoint.d/10-dashboard-auth.sh
RUN chmod +x /docker-entrypoint.d/10-dashboard-auth.sh
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
# Set DASHBOARD_USER / DASHBOARD_PASSWORD to require a login (recommended anywhere public).
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1

# Multi-stage: `dev` is the Vite dev server used by docker-compose
# (`target: dev`); the default (last) stage is the production image -- the
# built SPA served by nginx, which also reverse-proxies /api/ to the backend.

FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM deps AS dev
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host"]

FROM deps AS build
COPY . .
# Same-origin path, proxied by nginx below -- keeps the image independent of
# where it's deployed. Only override for a non-proxied setup.
ARG VITE_API_URL=/api
ENV VITE_API_URL=${VITE_API_URL}
RUN npm run build

FROM nginxinc/nginx-unprivileged:1-alpine
# Rendered by the image's entrypoint (envsubst over /etc/nginx/templates/*.template).
# NGINX_ENVSUBST_FILTER restricts substitution to our own variables so nginx's
# own $uri/$host/... survive untouched.
ENV API_UPSTREAM=http://aitana-api:8080 \
    MAX_BODY_SIZE=200m \
    NGINX_ENVSUBST_FILTER="^(API_UPSTREAM|MAX_BODY_SIZE)$"
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080

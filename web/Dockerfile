# Builds the web app and serves it as static files. Used by the Railway "web" service (root directory: web).

FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm install --no-audit --no-fund
COPY . .
# Vite bakes these into the JavaScript at build time. On Railway, set them as variables on the web service;
# Railway passes service variables to the build for every ARG declared here.
ARG VITE_API_URL
ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_PUBLISHABLE_KEY
ARG VITE_SUPABASE_ANON_KEY
ARG VITE_VAPID_PUBLIC_KEY
ARG VITE_GOOGLE_SIGNIN
RUN test -n "$VITE_API_URL" || (echo "VITE_API_URL must be set (the API's https address)" && exit 1)
RUN npm run build

FROM caddy:2-alpine
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
# Caddy listens on $PORT (Railway) or 8080.

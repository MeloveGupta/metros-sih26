# MetroScan frontend image: build with Vite, serve with nginx.
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
# SPA fallback + proxy API to the api service.
RUN printf 'server {\n\
  listen 80;\n\
  root /usr/share/nginx/html;\n\
  location /scan  { proxy_pass http://api:8000; }\n\
  location /scans { proxy_pass http://api:8000; }\n\
  location /auth  { proxy_pass http://api:8000; }\n\
  location /health { proxy_pass http://api:8000; }\n\
  location / { try_files $uri /index.html; }\n\
}\n' > /etc/nginx/conf.d/default.conf
EXPOSE 80

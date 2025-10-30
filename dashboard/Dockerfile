# Build stage
FROM node:20-alpine AS build
WORKDIR /dashboard
COPY dashboard/package*.json ./
RUN npm ci
COPY dashboard/ .
RUN npm run build

# Serve with a tiny HTTP server
FROM nginx:alpine
WORKDIR /usr/share/nginx/html
COPY --from=build /dashboard/dist ./
# Basic security headers (optional)
RUN printf 'add_header X-Content-Type-Options "nosniff";\nadd_header X-Frame-Options "SAMEORIGIN";\n' >> /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]

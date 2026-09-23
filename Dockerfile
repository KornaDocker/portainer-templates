FROM nginx:stable-alpine

COPY templates.json /usr/share/nginx/html/templates.json
# Served alongside the templates so the site can flag known-broken apps
COPY overrides.json /usr/share/nginx/html/overrides.json
COPY index.html /usr/share/nginx/html/index.html

EXPOSE 80

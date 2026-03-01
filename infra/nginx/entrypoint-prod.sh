#!/bin/sh
set -e
envsubst '${DOMAIN}' < /etc/nginx/templates/prod.conf > /etc/nginx/conf.d/default.conf
exec nginx -g "daemon off;"

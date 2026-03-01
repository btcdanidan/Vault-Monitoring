#!/usr/bin/env bash
# One-time initial Let's Encrypt certificate acquisition for production.
# Run from infra/: ./scripts/init-ssl.sh [DOMAIN] [EMAIL]
# Requires: port 80 free (stop nginx first: docker compose -f docker-compose.yml -f docker-compose.prod.yml stop nginx)

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$INFRA_DIR"

DOMAIN="${DOMAIN:-$1}"
EMAIL="${CERTBOT_EMAIL:-$2}"

if [[ -z "$DOMAIN" || -z "$EMAIL" ]]; then
  echo "Usage: DOMAIN=example.com CERTBOT_EMAIL=admin@example.com $0"
  echo "   Or: $0 example.com admin@example.com"
  exit 1
fi

echo "Stopping nginx so certbot can bind port 80..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop nginx 2>/dev/null || true

echo "Requesting certificate for $DOMAIN..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm -p 80:80 \
  --entrypoint certbot certbot certonly \
  --standalone \
  -d "$DOMAIN" \
  --email "$EMAIL" \
  --agree-tos \
  --non-interactive

echo "Starting nginx with SSL..."
export DOMAIN
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d nginx

echo "Done. HTTPS should be available at https://$DOMAIN"
echo "Certbot renewal runs automatically in the certbot container every 12h."

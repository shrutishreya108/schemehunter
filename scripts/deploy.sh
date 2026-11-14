#!/bin/sh
# Deploy to the GoDaddy VPS: ssh → git pull → docker compose up -d --build → migrations → curl /healthz.
#
#   VPS=deploy@203.0.113.10 DOMAIN=example.in sh scripts/deploy.sh
#
# Env:
#   VPS      ssh target (user@host). SSH key auth only (see docs/GODADDY_CHECKLIST.md item 1).
#   DOMAIN   public domain, for the final https health check.
#   APP_DIR  checkout on the VPS (default /srv/schemehunter). It must already contain .env (chmod 600).
#   BRANCH   git branch to deploy (default main).
set -eu
: "${VPS:?set VPS=user@host}" "${DOMAIN:?set DOMAIN=your-domain}"
APP_DIR="${APP_DIR:-/srv/schemehunter}"
BRANCH="${BRANCH:-main}"

echo "==> deploying $BRANCH to $VPS:$APP_DIR"
ssh -o BatchMode=yes "$VPS" "set -eu
  cd '$APP_DIR'
  test -f .env || { echo '.env missing on the VPS (copy .env.example, fill it, chmod 600)'; exit 1; }
  git fetch --quiet origin '$BRANCH'
  git checkout --quiet '$BRANCH'
  git pull --ff-only --quiet origin '$BRANCH'
  docker compose up -d --build
  docker compose exec -T web python -c 'from app.db import run_migrations; print(\"migrations applied:\", run_migrations())'
  docker compose ps"

echo "==> https://$DOMAIN/healthz"
i=0
until curl -fsS "https://$DOMAIN/healthz"; do
  i=$((i + 1))
  [ "$i" -ge 10 ] && { echo; echo "health check failed after 10 tries"; exit 1; }
  sleep 3
done
echo

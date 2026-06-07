#!/usr/bin/env bash
#
# Deploy / update the Stücklistenagent on a Linux VPS via Docker Compose.
# Run this FROM the project directory ON the VPS (after the code is present).
#
#   ./deploy.sh
#
# Prereqs on the VPS: Docker Engine + Docker Compose plugin, a domain whose
# A-record points at this server, ports 80 + 443 open, and a filled-in .env.
set -euo pipefail

COMPOSE_FILE="docker-compose.prod.yml"
cd "$(dirname "$0")"

echo "==> Stücklistenagent deploy"

# --- 1. sanity checks ------------------------------------------------------
command -v docker >/dev/null || { echo "ERROR: docker not installed"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "ERROR: 'docker compose' plugin missing"; exit 1; }
[ -f "$COMPOSE_FILE" ] || { echo "ERROR: $COMPOSE_FILE not found — run from project root"; exit 1; }
[ -f ".env" ] || { echo "ERROR: .env missing. Copy .env.deploy.example -> .env and fill it in."; exit 1; }

# Required .env keys for compose interpolation + Azure.
for key in DOMAIN ACME_EMAIL AZURE_OPENAI_ENDPOINT AZURE_OPENAI_KEY; do
  grep -qE "^${key}=.+" .env || { echo "ERROR: ${key} is not set in .env"; exit 1; }
done

# --- 2. persistent data dir ------------------------------------------------
mkdir -p data/uploads data/exports
echo "==> data/ ready (jobs.db is created on first run)"

# --- 3. build + (re)start --------------------------------------------------
echo "==> Building images (frontend bakes NEXT_PUBLIC_API_URL = https://\$DOMAIN)"
docker compose -f "$COMPOSE_FILE" build

echo "==> Starting stack"
docker compose -f "$COMPOSE_FILE" up -d

# --- 4. status -------------------------------------------------------------
echo "==> Containers:"
docker compose -f "$COMPOSE_FILE" ps
DOMAIN_VALUE="$(grep -E '^DOMAIN=' .env | cut -d= -f2-)"
echo
echo "==> Done. Open: https://${DOMAIN_VALUE}"
echo "    Login: admin / admin  (change via LOGIN_ADMIN_PASSWORD in .env, then re-run)"
echo "    Logs:  docker compose -f ${COMPOSE_FILE} logs -f"

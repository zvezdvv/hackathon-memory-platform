#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]]; then
  echo 'Usage: bash deploy/init-env.sh <public IPv4 address or hostname>' >&2
  exit 1
fi

cd -- "$(dirname -- "$0")/.."
umask 077
set -o noclobber

cat > .env <<EOF
PUBLIC_HOST=$1
OPENROUTER_KEY=
JWT_SECRET=$(openssl rand -hex 32)
JWT_REFRESH_SECRET=$(openssl rand -hex 32)
CREDS_KEY=$(openssl rand -hex 32)
CREDS_IV=$(openssl rand -hex 16)
MONGO_PASSWORD=$(openssl rand -hex 24)
MEILI_MASTER_KEY=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -hex 24)
EOF

#!/bin/sh
# Nightly, from cron on the VPS HOST (LLD §18):
#   pg_dump -Fc | age -r <pubkey> > /backups/$(date +%F).dump.age  + copy off-box
#   0 2 * * *  cd /srv/schemehunter && BACKUP_AGE_RECIPIENT=age1... sh scripts/backup.sh
# pg_dump runs inside the postgres:16 container so client and server versions always match.
# Needs `age` on the host. Env: BACKUP_AGE_RECIPIENT (required), BACKUP_DIR (default /backups),
# BACKUP_REMOTE (optional scp target, e.g. user@host:/srv/backups/).
set -eu
: "${BACKUP_AGE_RECIPIENT:?set the age public key}"
dir="${BACKUP_DIR:-/backups}"
mkdir -p "$dir"
out="$dir/$(date +%F).dump.age"
docker compose exec -T postgres pg_dump -Fc -U app schemehunter | age -r "$BACKUP_AGE_RECIPIENT" > "$out"
echo "wrote $out"
if [ -n "${BACKUP_REMOTE:-}" ]; then
  scp -q "$out" "$BACKUP_REMOTE"
  echo "copied to $BACKUP_REMOTE"
fi

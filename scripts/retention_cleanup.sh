#!/bin/sh
# Daily (cron): delete document files for CLOSED cases older than RETENTION_DAYS. Calls the app job.
#   30 3 * * *  cd /srv/schemehunter && make retention-cleanup
set -eu
python -c "from app.clock.handlers import run_retention; print('removed', run_retention(), 'document(s)')"

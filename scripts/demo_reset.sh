#!/bin/sh
# make demo-reset: truncate → load schemes, personas, histories, jobs → print case links. Target < 10 s.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
want="$(awk '/^demo_clock_scale:/ {print $2}' "$ROOT/seed/showcase.yaml")"
have="${DEMO_CLOCK_SCALE:-1}"
if [ "$have" != "$want" ]; then
  echo "warning: DEMO_CLOCK_SCALE is $have but showcase.yaml wants $want."
  echo "         set DEMO_CLOCK_SCALE=$want in .env and restart web/worker before the demo."
fi
cd "$ROOT"
python -m app.seed --reset

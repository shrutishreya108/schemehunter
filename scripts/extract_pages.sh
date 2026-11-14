#!/bin/sh
# pdftotext -layout per page → rulebook/sources/<scheme>/pages/NN.txt  (make extract-pages)
# Needs poppler-utils (pdfinfo, pdftotext). Re-run after replacing any guidelines.pdf.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
found=0
for pdf in "$ROOT"/rulebook/sources/*/guidelines.pdf; do
  [ -f "$pdf" ] || continue
  found=1
  dir="$(dirname "$pdf")/pages"
  mkdir -p "$dir"
  rm -f "$dir"/[0-9][0-9].txt "$dir"/[0-9][0-9][0-9].txt
  pages="$(pdfinfo "$pdf" | awk '/^Pages:/ {print $2}')"
  i=1
  while [ "$i" -le "$pages" ]; do
    pdftotext -layout -f "$i" -l "$i" "$pdf" "$dir/$(printf '%02d' "$i").txt"
    i=$((i + 1))
  done
  echo "$(basename "$(dirname "$pdf")"): $pages pages"
done
[ "$found" -eq 1 ] || echo "no guidelines.pdf found under rulebook/sources/*/ (add the official PDFs first)"

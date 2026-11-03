#!/usr/bin/env bash
# Download the public credit-card fraud dataset (ULB / Kaggle "creditcardfraud").
#
# Default source is OpenML's mirror (no account needed, ~150 MB CSV, 284,807 rows).
# If you have the Kaggle CLI configured you can use the original instead:
#   kaggle datasets download -d mlg-ulb/creditcardfraud -p data --unzip
#
# Usage: scripts/download_data.sh [output-path]
set -euo pipefail

OUT="${1:-data/creditcard.csv}"
URL="https://www.openml.org/data/get_csv/1673544/phpKo8OWT"

mkdir -p "$(dirname "$OUT")"
if [ -s "$OUT" ]; then
  echo "already present: $OUT ($(wc -l < "$OUT") lines)"
  exit 0
fi

echo "downloading $URL -> $OUT"
curl -fSL --retry 3 --retry-delay 5 -o "$OUT.part" "$URL"
mv "$OUT.part" "$OUT"
echo "done: $(wc -l < "$OUT") lines"
head -c 200 "$OUT"; echo

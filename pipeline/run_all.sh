#!/usr/bin/env bash
# Reconstruit toutes les données de l'application (≈ 10-20 min au premier lancement, cache ensuite).
# Prérequis : Python 3.11+ avec pandas, numpy, requests, openpyxl ; `npm install` fait (mapshaper).
set -euo pipefail
cd "$(dirname "$0")/.."
python3 pipeline/sources/communes.py
for s in pipeline/sources/*.py; do
  [ "$(basename "$s")" = "communes.py" ] && continue
  echo "== $s"
  python3 "$s" || echo "!! échec de $s (les autres sources continuent)"
done
python3 pipeline/build_transit.py
python3 pipeline/build_geometry.py
python3 pipeline/build_communes.py

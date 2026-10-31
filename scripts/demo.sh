#!/usr/bin/env bash
# Демо без затрат: два mock-прогона на чистой памяти + бенч памяти. Всё пишется во временную папку.
set -euo pipefail
cd "$(dirname "$0")/.."
T=$(mktemp -d)
python3 -m factory plan briefs/demo_earrings.yaml
python3 -m factory run briefs/demo_earrings.yaml --memory "$T/mem.yaml" --runs "$T/runs" --seed 1
python3 -m factory run briefs/demo_earrings.yaml --memory "$T/mem.yaml" --runs "$T/runs" --seed 2
python3 -m factory stats --runs "$T/runs"
python3 -m factory memory --memory "$T/mem.yaml"
python3 -m factory bench briefs/demo_earrings.yaml --seeds 20 --out "$T"
echo "всё в $T"

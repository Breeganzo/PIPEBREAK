#!/usr/bin/env bash
# Resume the evaluation sweep across all configured models.
#
# The free tier allows roughly 200,000 tokens per model per day and a task
# costs 30,000-40,000, so a full 16-task sweep does not fit in one sitting.
# This script is safe and cheap to re-run: completed tasks are loaded from
# results/scores_<model>.json and skipped, and a model whose daily allowance is
# gone stops cleanly without recording a score. Run it once a day until the
# leaderboard is full.
#
# Each model has its own rate-limit bucket, so they run in parallel.
#
#   ./sweep.sh            # resume every configured model
#   ./sweep.sh --status   # just show where things stand
set -euo pipefail
cd "$(dirname "$0")"

PY=./.venv/bin/python
MODELS=$($PY -c "from pipebreak import config; print(' '.join(config.model_list()))")

status() {
  $PY - <<'PY'
import json, pathlib
from pipebreak import config, evaluate
total = len(list(config.RESULTS.glob("scores_*.json")))
if not total:
    print("no results yet")
else:
    for f in sorted(config.RESULTS.glob("scores_*.json")):
        scores = json.loads(f.read_text())
        name = scores[0]["model"] if scores else f.stem
        print(f"  {name:24s} {len(scores):2d}/16 tasks scored")
PY
}

if [[ "${1:-}" == "--status" ]]; then
  status
  exit 0
fi

if pgrep -f "pipebreak run" > /dev/null; then
  echo "A sweep is already running. Current state:"
  status
  exit 1
fi

mkdir -p results
for m in $MODELS; do
  slug=$(echo "$m" | tr '/' '_')
  nohup $PY -m pipebreak run "$m" > "results/sweep_$slug.log" 2>&1 &
  echo "resumed $m (pid $!)"
done

echo
echo "Running in the background. Watch with:"
echo "  ./sweep.sh --status"
echo "  tail -f results/sweep_*.log"
echo "  python -m pipebreak report"

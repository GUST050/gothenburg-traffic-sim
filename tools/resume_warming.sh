#!/bin/bash
# Supervisor for the annual warming. Touches NO source file: it only re-invokes
# the same --execute command, so the plan key and the durable bank are safe.
#
# WHY: sumo aborts with SIGABRT (libmalloc heap corruption) roughly once per
# ~39,000 units, and execute_population is fail-closed, so one crash halts the
# whole run. Retrying that class inside the tool would edit a BOUND source and
# orphan every unit already banked. Resuming from outside cannot.
#
# Stops for real if two consecutive resumes make NO progress -- so a genuine,
# deterministic failure is never masked by an infinite retry loop.
#
# Holds sleep off for its own lifetime. A sleeping Mac does not corrupt the
# durable bank, but it does destroy the wall-clock number the run exists to
# measure against WARMING_PLAN section 2's revert gate.
cd "$(dirname "$0")/.." || exit 1
KEY=$(python3 -c "import json;print(json.load(open('validation/annual_warm_plan_2027.json'))['content_key'])")
MAX=${MAX_RESUMES:-25}

# Keep the machine awake for as long as this supervisor lives, unless something
# already is (the caller may have armed its own).
if ! pgrep -f "caffeinate -i -w $$" >/dev/null 2>&1; then
  caffeinate -i -w $$ >/dev/null 2>&1 &
fi

prev=-1; stuck=0
for i in $(seq 1 "$MAX"); do
  # succeeded AND total from ONE call: the total is a property of the plan, and
  # hardcoding it would silently mis-detect completion after a coverage change.
  read -r ok total <<EOF
$(python3 tools/populate_annual_warming.py --status --state-workers 3 2>/dev/null \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['succeeded'],d['total'])" 2>/dev/null)
EOF
  [ -z "$ok" ] || [ -z "$total" ] && { echo "kunde inte läsa status; avbryter"; exit 1; }
  if [ "$ok" = "$prev" ]; then
    stuck=$((stuck+1))
    if [ "$stuck" -ge 2 ]; then
      echo "INGEN PROGRESS på två försök i rad vid $ok units — verkligt fel, avbryter"; exit 1
    fi
  else
    stuck=0
  fi
  prev=$ok
  if [ "$ok" -ge "$total" ]; then echo "KLART: $ok/$total units"; exit 0; fi
  TS=$(date +%Y%m%d-%H%M%S); LOG="runs/annual-warm-logs/2027-$TS.log"
  echo "$LOG" > runs/annual-warm-logs/latest-path.txt
  echo "[$(date '+%F %T')] resume $i/$MAX vid $ok units -> $LOG"
  python3 tools/populate_annual_warming.py --execute --state-workers 3 --plan-key "$KEY" \
    > "$LOG" 2>&1
  rc=$?
  echo "[$(date '+%F %T')] avslutade med kod $rc"
  [ $rc -eq 0 ] && { echo "KLART"; exit 0; }
  sleep 10
done
echo "nådde MAX_RESUMES=$MAX"; exit 1

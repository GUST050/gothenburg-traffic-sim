#!/bin/zsh
set -eu

repo_root="/Users/gt/Documents/gs-project"
run_id="20260831-003825-46483"
auth_path="/Users/gt/.codex/auth.json"
poll_seconds=3
max_wait_seconds=43200

auth_fingerprint() {
  if [[ -f "$auth_path" ]]; then
    stat -f '%i:%m:%z' "$auth_path"
  else
    print -- "missing"
  fi
}

started_at=$(date +%s)
initial_fingerprint=$(auth_fingerprint)
print -- "Paused run $run_id; waiting for Codex credential cache to change."

while true; do
  now=$(date +%s)
  if (( now - started_at >= max_wait_seconds )); then
    print -- "Timed out waiting for an account change; run remains INTERRUPTED."
    exit 2
  fi

  current_fingerprint=$(auth_fingerprint)
  if [[ "$current_fingerprint" != "missing" && \
        "$current_fingerprint" != "$initial_fingerprint" ]]; then
    # Require a stable, authenticated cache. Never read or print its contents.
    sleep 5
    stable_fingerprint=$(auth_fingerprint)
    if [[ "$stable_fingerprint" == "$current_fingerprint" ]] && \
       codex login status >/dev/null 2>&1; then
      print -- "New authenticated Codex cache detected; resuming $run_id."
      cd "$repo_root"
      resume_attempt=1
      while (( resume_attempt <= 3 )); do
        if ./ai-flow \
          --config .ai-flow/config.complete-subhour.toml \
          --resume-run "$run_id" \
          --fresh-stage \
          --allow-dirty; then
          exit 0
        else
          result=$?
        fi
        status=$(python3 -c "import json; print(json.load(open('.ai-flow/runs/$run_id/status.json')).get('status', 'ERROR'))")
        if [[ "$status" != "ERROR" && "$status" != "INTERRUPTED" ]]; then
          exit "$result"
        fi
        print -- "Transient $status; retrying the persisted stage ($resume_attempt/3)."
        resume_attempt=$(( resume_attempt + 1 ))
        sleep 15
      done
      print -- "Three automatic resume attempts failed; evidence remains persisted."
      exit 3
    fi
  fi
  sleep "$poll_seconds"
done

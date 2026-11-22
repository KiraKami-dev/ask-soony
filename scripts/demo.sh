#!/usr/bin/env bash
# A rehearsal instance next to the live one: all cases open, no real supplier email.
#   scripts/demo.sh snapshot   build the demo snapshot from the live state (decisions undone)
#   scripts/demo.sh reset      restore the snapshot and (re)start the demo server on :8001
#   CALLS=twilio scripts/demo.sh reset   same, with real notification calls (paid, ~USD 0.38/min)
#   scripts/demo.sh stop       stop the demo server
# Live keeps running on :8000 with data/state; the demo uses data/demo/state.
set -euo pipefail
cd "$(dirname "$0")/.."
SNAP=data/demo/snapshot
STATE=data/demo/state
PORT=${ASK_DEMO_PORT:-8001}

snapshot() {
  rm -rf "$SNAP" && mkdir -p "$SNAP"
  # What the detectors found stays; what people did with it goes.
  for f in data/state/*.json data/state/*.pem; do
    case "$(basename "$f")" in
      *.before-*|*.alert-agent-*|*.globe-*) ;;                       # old backups
      actions.json|decision_status.json|routine_proposals.json|routine_drafts.json|\
      site_questions.json|count_tokens.json|floor_counts.json|signoff*.json|\
      replies_seen.json|teams_posted.json|events.json|dry_outbox.json|voice_tokens.json) ;;
      *) cp "$f" "$SNAP/" ;;
    esac
  done
  [ -d data/state/floor_photos ] || true
  # Routines stay, switched off, one per name: the demo shows switching one on.
  uv run python - "$SNAP" <<'PY'
import json, sys, pathlib
p = pathlib.Path(sys.argv[1]) / "routines.json"
if p.exists():
    items = json.loads(p.read_text())
    items = items if isinstance(items, list) else list(items.values())
    seen, keep = set(), []
    for r in items:
        if r.get("name") in seen:
            continue
        seen.add(r.get("name"))
        keep.append({**r, "enabled": False, "runs": [], "seen": [], "handled": 0})
    p.write_text(json.dumps(keep, indent=2))
PY
  echo "snapshot: $(ls "$SNAP" | wc -l | tr -d ' ') files in $SNAP"
}

stop() { tmux kill-session -t ask-soony-demo 2>/dev/null || true; }

reset() {
  [ -d "$SNAP" ] || snapshot
  stop
  rm -rf "$STATE" && mkdir -p "$STATE" && cp -R "$SNAP"/. "$STATE"/
  uv run python -m ask_soony.card_alerts forget "$STATE"  # phone alerts start afresh too
  PW="$(grep '^ASK_DASHBOARD_PASSWORD=' .env | cut -d= -f2-)"
  tmux new-session -d -s ask-soony-demo "cd $PWD && \
    ASK_STATE_DIR=$STATE ASK_MAIL_DRY=on ASK_OUTBOX=off ASK_REPLY_POLL=off ASK_TEAMS=off \
    ASK_CALL_PROVIDER=${CALLS:-dry_run} SOONY_PORTAL_TOKEN= ASK_BACKGROUND=on ASK_LLM_PROVIDER=none \
    ASK_DASHBOARD_PASSWORD='$PW' ASK_APP_URL=https://ask-soony-demo.stenglein.dev \
    ASK_PUSH_CONTACT=https://ask-soony-demo.stenglein.dev ASK_PUBLIC_URL=https://ask-soony-demo.stenglein.dev \
    uv run uvicorn ask_soony.api:app --port $PORT 2>&1 | tee -a data/logs/demo.log"
  echo "demo: http://127.0.0.1:$PORT (state reset from $SNAP)"
}

case "${1:-reset}" in
  snapshot) snapshot ;;
  reset) reset ;;
  stop) stop ;;
  *) echo "usage: $0 snapshot|reset|stop" >&2; exit 2 ;;
esac

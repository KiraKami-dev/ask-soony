# ask-soony

Our entry for the **Lio challenge "Keep the Line Running"** at START Hack Munich 2026.

Soony, a (fictional) games publisher, launches Grand Heist 6 on **Sunday 22 Nov, 20:00**. Every copy is packed in one distribution center in Hamburg, six suppliers deliver the parts, and the last 20% is not packed yet. Soony's data lives in SAP, spreadsheets, PDFs and scans, in four languages. Soony's head of procurement: "We find out at the dock."

We build agents that read that data, decide what is at risk and act on it before it is too late.

## Status

Work in progress. The product direction is being decided; this README will describe it once it is fixed.

## Getting started

1. Clone the repo and install [uv](https://docs.astral.sh/uv/), then `uv sync --group dev`
2. Create your `.env` from the template: `cp .env.example .env`. It is gitignored; never commit it or paste keys in chat.
3. Put the keys into `.env`:

   | Variable | Where to get it | Needed for |
   |---|---|---|
   | `SOONY_PORTAL_TOKEN` | Portal → Settings → API token | Everything (download, poll, watch) |
   | `ANTHROPIC_API_KEY` | [Claude Console](https://console.anthropic.com/) → API keys | `analyze` and `watch` with the default Claude backend |
   | `ASK_LLM_PROVIDER` + `ASK_LLM_API_KEY` | OpenAI key, or empty for a local llama.cpp server | Only if you use another backend (see below) |

   `SOONY_PORTAL_URL` is already filled in by the template.
4. Set up Entire so your agent sessions are tracked: see [AGENTS.md](AGENTS.md#entire-session-tracking)

### Download the portal data once

Needs only `SOONY_PORTAL_TOKEN`, no LLM key:

```bash
uv run ask-soony download              # all current files into data/portal/, under their portal paths
uv run ask-soony download some/folder  # or into another folder
```

Run it again any time to bring the folder up to date: only new or changed files are fetched (the first run uses `archive.zip`). Files removed from the portal stay in the folder. Every version also lands in the version store (`data/store/`, see below). All of `data/` is gitignored: the data is confidential.

## Platform: decision inbox (`ask-soony run`)

One service: FastAPI serves the built UI (`web/dist`) and a JSON API over `data/state/`, and a background thread runs the watcher loop: poll the portal, mirror new files, record each new or changed file as a drop, verify and predict, then dispatch each new decision to the internal alert policy. The policy notifies, emails or calls the responsible Soony contact; supplier drafts remain approval-gated in the inbox. Invalid dispatch data or an incomplete alert policy raises an error instead of inventing an action.

```bash
cd web && npm ci && npm run build && cd ..   # build the UI once
uv run ask-soony run --port 8000             # UI + API + watcher loop (needs SOONY_PORTAL_TOKEN to poll)
uv run ask-soony serve --port 8000           # UI + API only, over the files already on disk
uv run ask-soony drop suppliers/<id>/notices/x.pdf [--from local.pdf]   # rehearse a live drop
uv run python scripts/seed_demo_state.py [--calm | --drop]   # dev only: placeholder state from the portal files
```

Before `run`, create `data/alert-policy.json` from `docs/alert-policy.example.json` and replace its example contacts. Its critical route decides whether the internal approver is notified, emailed, or called; it never sends a supplier message automatically.

The UI reads the pipeline's `data/state/dispatch.json` (verifier decisions, editions, parts), the predictor's `data/state/impacts.json` and the shared shapes in `data/state/`. Source documents come from the image recognizer's readings in `data/store/image_recognizer/` (without them the art-book and statue findings cannot be made: run `python -m ask_soony.verifier.image_recognizer` first, or copy a teammate's readings). Each ready message in a decision becomes an action; one that commits money goes to the policy's approver of exceptions, routine supplier questions stay with the buyer. The seed script is only a fallback when no pipeline output exists. Slack and the time a line stops come from the launch plan's shift table, read at runtime (`src/ask_soony/launch.py`). Clicks write only to `data/state/` (`actions.json`, `decision_status.json`, `trust.json`); nothing is sent from the UI.

### Deploy

`Dockerfile` builds the UI and runs `ask-soony run` on `$PORT`. No portal data is baked in; set `SOONY_PORTAL_TOKEN` (and an LLM key for the predictor) as secrets on the host. For example with Fly.io: `fly launch --no-deploy`, `fly secrets set SOONY_PORTAL_TOKEN=...`, `fly deploy`. Render: new Web Service from this repo, runtime Docker, same env vars.

## Data watcher (`src/ask_soony`)

A fixed loop polls the portal, keeps every file version and lets an LLM agent analyse each change.

1. **Poll** `GET /api/v1/files` and compare SHA-256 checksums. New or replaced files are downloaded (the first sync uses `archive.zip`) and stored by checksum under `data/store/blobs/`; nothing is overwritten.
2. **Log** every version, poll, analysis and finding in SQLite (`data/store/ask-soony.db`), so each finding points at the exact file version behind it.
3. **Analyse** each new or changed file with an agent. It reads the file (text, or page images for scans and photos), cross-checks other files with tools (`list_files`, `read_file`, `search_files`, `list_open_findings`) and submits a summary plus findings with evidence. Scans get a transcription that later searches can use.
4. **Notify** through console, macOS notifications, a Discord webhook or a generic JSON webhook.

To start the history from an earlier state, seed an empty store first: `uv run ask-soony import-snapshot data/archive.zip --at 2026-01-01T10:00:00+01:00` (a portal `archive.zip` or a folder with portal paths). Later polls then show every drop since that state.

The first poll stores the current state as a baseline without analysing it; only later changes are analysed. `analyze --path <prefix>` queues existing files on purpose.

```bash
uv run ask-soony download       # one-off: fetch everything into data/portal/ (no LLM needed)
uv run ask-soony poll           # one poll; the first one stores the baseline
uv run ask-soony analyze --path suppliers/SUP-0028_Karton_Nowak/notices/   # analyse chosen files
uv run ask-soony watch          # poll, analyse and notify every ASK_INTERVAL_SECONDS
uv run ask-soony refresh soony/erp/stock_snapshot.csv  # refresh verifier + decision cards
uv run ask-soony predict        # trace notices/news through the supply network
uv run ask-soony findings       # open findings
uv run ask-soony status
uv run pytest
```

### Dashboard: what changed, when

Every new or changed file version gets a "what changed" summary without any LLM: CSV files are compared row by row (rows added, removed, changed with old → new values; columns added, removed or renamed), Excel files cell by cell, everything else as a text diff. The dashboard shows each poll with its changes, every file's version history with diffs, the agent's findings and the supplier mail log (`data/outbox/`, `data/inbox/`).

```bash
uv run ask-soony watch-dashboard --interval 300 # poll every 5 min + dashboard on http://127.0.0.1:8765
uv run ask-soony dashboard              # dashboard only
```

The product runs as one server: the decision inbox app with the call webhooks (`/hooks/*`) and, with `ASK_BACKGROUND=on`, the portal watcher, alert escalation and the supplier reply poller as background loops:

```bash
(cd web && npm run build)
ASK_BACKGROUND=on uv run uvicorn ask_soony.api:app --port 8000
```

With `ASK_DASHBOARD_PASSWORD` set in the environment, the app asks for it on a login page (30-day cookie, so it works as a home screen app) or via basic auth; the call webhooks check their own signature or per-alert token instead.

Set `ASK_DASHBOARD_PASSWORD` in `.env` (HTTP basic auth, any user name). The dashboard listens on localhost only and refuses other addresses without a password. To share it, put a tunnel in front, for example a Cloudflare Tunnel with its own config file:

```yaml
# ~/.cloudflared/ask-soony.yml
protocol: http2            # QUIC is often blocked on event and campus networks
tunnel: <tunnel id>
credentials-file: /path/to/<tunnel id>.json
ingress:
  - hostname: ask-soony.example.com   # one level below the zone, so the free certificate covers it
    service: http://localhost:8765
  - service: http_status:404
```

```bash
cloudflared tunnel create ask-soony
cloudflared tunnel --config ~/.cloudflared/ask-soony.yml route dns <tunnel id> ask-soony.example.com
cloudflared tunnel --config ~/.cloudflared/ask-soony.yml run
```

Pass `--config` to `route dns` too: without it, `cloudflared` takes the tunnel from the default `config.yml` and may attach the hostname to a different tunnel.

### Alerts: notify, call, escalate

Other modules (verification, prediction, the watcher) raise alerts; the alert module decides how loud they get and who has to decide.

- **Criticality**: the producer sets a severity; with a deadline and `--impact launch` it is raised automatically (less than `critical_hours` left = critical, less than three times that = at least high).
- **Escalation policy** (`ASK_ALERT_POLICY`, default `data/alert-policy.json`, gitignored because it holds phone numbers; see `docs/alert-policy.example.json`): contacts, which channels each severity uses, the order of people to try and how many minutes each gets to decide before the next one is notified.
- **Channels**: dashboard (`/alerts`, with Approve / Escalate / Reject buttons), email (only with `ASK_ALERT_EMAIL=on`), call. Calls go through `ASK_CALL_PROVIDER`:
  - `dry_run` (default): logs what would be said, no credentials, no cost
  - `twilio`: reads the alert and takes a keypad answer (1 approve, 2 escalate, 3 reject), posted to `/hooks/twilio/<id>`. Needs `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`. Works on a free Twilio trial: Twilio fetches the call script from `/hooks/twilio/twiml/<id>` (trials accept only a URL, not inline TwiML), uses the shared trial number shown under Voice → Try out Voice, and can only call verified numbers in the signup country
  - `openai`: Twilio calls the contact and hands the answered call to OpenAI's gpt-realtime over SIP (`<Dial><Sip>`). OpenAI posts `realtime.call.incoming` to `/hooks/openai` (signature-checked with `OPENAI_WEBHOOK_SECRET`); we accept with the alert as instructions and a `record_decision` function and follow the call on the sideband WebSocket. Needs `OPENAI_API_KEY`, `OPENAI_PROJECT_ID` and a project-level webhook (Settings → Project → Webhooks, event `realtime.call.incoming`). The model's sentences and the transcribed answers are logged in the alert history
  - `elevenlabs`: a voice agent talks the alert through. Needs `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_PHONE_NUMBER_ID` (a Twilio number imported into ElevenLabs)
- **Webhooks** need `ASK_PUBLIC_URL` (the tunnel). They bypass the dashboard password and are authenticated by a secret token per alert.

```bash
uv run ask-soony alert raise --severity high --impact launch --deadline 2026-01-10T06:00:00+01:00 \
  --title "Part short for launch" --message "5,900 missing" --action "Order 8,000 from supplier X"
uv run ask-soony alert list
uv run ask-soony alert decide 1 approve
uv run ask-soony watch-dashboard # also escalates undecided alerts every 30 seconds
```

ElevenLabs agent setup (dashboard): the call passes `contact_name`, `alert_id`, `alert_title`, `alert_message`, `proposed_action`, `deadline`, `decision_url` and `secret__alert_token` as dynamic variables. Use them in the prompt (for example "You are the procurement alert assistant. Tell {{contact_name}}: {{alert_title}}. {{alert_message}} Proposed action: {{proposed_action}}. Ask whether to approve, escalate or reject."), and add a server tool `record_decision` that POSTs JSON `{"alert_id": "{{alert_id}}", "decision": "<approve|escalate|reject>", "token": "{{secret__alert_token}}"}` to `{{decision_url}}`.

LLM backends (`ASK_LLM_PROVIDER`): `anthropic` (Claude via the Anthropic SDK, default `claude-opus-5-5`), `openai` (OpenAI), `llamacpp` (any OpenAI-compatible server, e.g. `llama-server --jinja` with `ASK_LLM_BASE_URL=http://localhost:8080/v1`), `none` (poll and store only). Local models need vision support for scans.

## Change routing

All product code lives in `src/ask_soony/`. The watcher routes every new or changed portal
file through `verifier` and regenerates approval-gated decision cards. Changes under notices,
news, logistics, or shipments also run `predictor`. `dispatch` combines the refreshed results
into `data/state/dispatch.json`; it prepares notifications, emails, or calls for approval but
does not send external messages automatically.
## Run the dashboard locally

Prerequisites: [uv](https://docs.astral.sh/uv/) and Node 18 or newer.

```bash
scripts/dashboard.sh    # API on :8000, UI on http://localhost:5173, Ctrl-C stops both
```

It shows `web/fixtures/sample_state.json` (made-up data) until real output exists. To use real verifier output, run `uv run python -m verifier.act` on the verifier branch (it writes `data/store/decision_cards.json`, which the API picks up), or point `ASK_SOONY_STATE` at any file of that shape. Your clicks and the activity feed are stored in `data/state/` (gitignored).

## Data

All customer data comes from the Soony portal at runtime (`GET /api/v1/files`, `/api/v1/archive.zip`). It is confidential: use it only for this hackathon, never share it outside the team and never commit it (`data/` is gitignored).

## Hand-in (Sun 12:00)

- [ ] Working prototype, deployed
- [ ] Pitch deck
- [ ] Demo video, max 3 minutes
- [ ] This repo with a short README (how to run, what it does)

Live demo from Sun 14:00: 3 minutes of pitch, 2 minutes of questions.

## For coding agents

Read [AGENTS.md](AGENTS.md) first.

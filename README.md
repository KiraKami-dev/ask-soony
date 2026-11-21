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

One service: FastAPI serves the built UI (`web/dist`) and a JSON API over `data/state/`, and a background thread runs the watcher loop: poll the portal, mirror new files, record each new or changed file as a drop, and route the changed paths through the installed modules (`ask_soony.dispatch.refresh`: verifier and predictor). Missing modules or a failing step never stop the UI.

```bash
cd web && npm ci && npm run build && cd ..   # build the UI once
uv run ask-soony run --port 8000             # UI + API + watcher loop (needs SOONY_PORTAL_TOKEN to poll)
uv run ask-soony serve --port 8000           # UI + API only, over the files already on disk
uv run ask-soony drop suppliers/<id>/notices/x.pdf [--from local.pdf]   # rehearse a live drop
uv run python scripts/seed_demo_state.py [--calm | --drop]   # dev only: placeholder state from the portal files
```

The UI reads the verifier's `data/store/decision_cards.json`, the predictor's `data/state/impacts.json` and the shared shapes in `data/state/` (findings, impacts, decisions, claims, events). Slack and the time a line stops come from the launch plan's shift table, read at runtime (`src/ask_soony/launch.py`). Clicks write only to `data/state/` (`actions.json`, `decision_status.json`, `trust.json`); nothing is sent from the UI.

### Deploy

`Dockerfile` builds the UI and runs `ask-soony run` on `$PORT`. No portal data is baked in; set `SOONY_PORTAL_TOKEN` (and an LLM key for the predictor) as secrets on the host. For example with Fly.io: `fly launch --no-deploy`, `fly secrets set SOONY_PORTAL_TOKEN=...`, `fly deploy`. Render: new Web Service from this repo, runtime Docker, same env vars.

## Data watcher (`src/ask_soony`)

A fixed loop polls the portal, keeps every file version and lets an LLM agent analyse each change.

1. **Poll** `GET /api/v1/files` and compare SHA-256 checksums. New or replaced files are downloaded (the first sync uses `archive.zip`) and stored by checksum under `data/store/blobs/`; nothing is overwritten.
2. **Log** every version, poll, analysis and finding in SQLite (`data/store/ask-soony.db`), so each finding points at the exact file version behind it.
3. **Analyse** each new or changed file with an agent. It reads the file (text, or page images for scans and photos), cross-checks other files with tools (`list_files`, `read_file`, `search_files`, `list_open_findings`) and submits a summary plus findings with evidence. Scans get a transcription that later searches can use.
4. **Notify** through console, macOS notifications, a Discord webhook or a generic JSON webhook.

The first poll stores the current state as a baseline without analysing it; only later changes are analysed. `analyze --path <prefix>` queues existing files on purpose.

```bash
uv run ask-soony download       # one-off: fetch everything into data/portal/ (no LLM needed)
uv run ask-soony poll           # one poll; the first one stores the baseline
uv run ask-soony analyze --path suppliers/SUP-0028_Karton_Nowak/notices/   # analyse chosen files
uv run ask-soony watch          # poll, analyse and notify every ASK_INTERVAL_SECONDS
uv run ask-soony findings       # open findings
uv run ask-soony status
uv run pytest
```

LLM backends (`ASK_LLM_PROVIDER`): `anthropic` (Claude via the Anthropic SDK, default `claude-opus-5-5`), `openai` (OpenAI), `llamacpp` (any OpenAI-compatible server, e.g. `llama-server --jinja` with `ASK_LLM_BASE_URL=http://localhost:8080/v1`), `none` (poll and store only). Local models need vision support for scans.

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

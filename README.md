# ask-soony

Our entry for the **Lio challenge "Keep the Line Running"** at START Hack Munich 2026.

Soony, a (fictional) games publisher, launches Grand Heist 6 on **Sunday 22 Nov, 20:00**. Every copy is packed in one distribution center in Hamburg, six suppliers deliver the parts, and the last 20% is not packed yet. Soony's data lives in SAP, spreadsheets, PDFs and scans, in four languages. Soony's head of procurement: "We find out at the dock."

We build agents that read that data, decide what is at risk and act on it before it is too late.

## Status

Work in progress. The product direction is being decided; this README will describe it once it is fixed.

## Getting started

1. Clone the repo
2. Create a `.env` in the repo root with the team token from the portal's Settings:

   ```bash
   SOONY_PORTAL_TOKEN=your-team-token
   ```

3. Set up Entire so your agent sessions are tracked: see [AGENTS.md](AGENTS.md#entire-session-tracking)

Run instructions follow once the stack is in place.

## Data watcher (`src/ask_soony`)

A fixed loop polls the portal, keeps every file version and lets an LLM agent analyse each change.

1. **Poll** `GET /api/v1/files` and compare SHA-256 checksums. New or replaced files are downloaded (the first sync uses `archive.zip`) and stored by checksum under `data/store/blobs/`; nothing is overwritten.
2. **Log** every version, poll, analysis and finding in SQLite (`data/store/ask-soony.db`), so each finding points at the exact file version behind it.
3. **Analyse** each new or changed file with an agent. It reads the file (text, or page images for scans and photos), cross-checks other files with tools (`list_files`, `read_file`, `search_files`, `list_open_findings`) and submits a summary plus findings with evidence. Scans get a transcription that later searches can use.
4. **Notify** through console, macOS notifications, a Discord webhook or a generic JSON webhook.

The first poll stores the current state as a baseline without analysing it; only later changes are analysed. `analyze --path <prefix>` queues existing files on purpose.

```bash
uv sync --group dev
cp .env.example .env            # fill in SOONY_PORTAL_TOKEN and the LLM settings
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

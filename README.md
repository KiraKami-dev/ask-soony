# Lighthouse

**Lighthouse catches supply-chain surprises before they stop the line.**

For the Lio “Keep the Line Running” challenge, we built Lighthouse for Soony: a publisher racing to ship *Grand Heist 6*. Their launch data is spread across ERP exports, spreadsheets, supplier emails, shipping updates, PDFs, and scanned paperwork—often contradictory, often too late.

Lighthouse turns that mess into one clear question: **what needs to happen next to protect the launch?**

## From paperwork to action

Every number in the plan is a claim. Lighthouse checks it against the evidence behind it:

- A delivery note against a goods receipt
- A supplier confirmation against an open shipment
- An inspection report against stock that looks available
- A notice or logistics update against the parts and products it can affect

When the sources disagree, Lighthouse does not silently pick a winner. It shows the contradiction, the original evidence, the impact on packing, and the best next move.

Then it helps the team act: draft a supplier message, ask the warehouse for a count, route a decision to the right approver, or escalate a time-critical case. Supplier mail is sent only after approval and only to known contacts.

## What the team sees

The React decision inbox keeps the launch team focused on the next best action:

- **Today** — one evidence-backed decision at a time
- **Launch** — what is at risk, when a line could run dry, and which part is limiting
- **Suppliers** — open cases, shipments, and relationship context
- **Evidence** — the source document and the ERP or planning claim side by side
- **Routines** — repeatable work that can be suggested, reviewed, and gradually trusted

Behind the scenes, Lighthouse watches for new portal files, preserves every file version, verifies the data, calculates shortfalls, and traces relevant world events through the supply network.

## How it works

```text
Portal files → verify claims → calculate launch impact → decision inbox → approved action
```

The system is built to be trustworthy:

- Nothing about a supplier, part, quantity, or deadline is hard-coded.
- New or changed files are detected and processed at runtime.
- Contradictions stay visible, with their source and age.
- Supplier replies are tied back to the original case.
- Money or plan changes remain human-approved.

Customer data stays local in gitignored `data/`. It is confidential hackathon material and must not be committed or shared.

## Run it

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- Node.js (the production image uses Node 22)
- A Soony portal token for live data

```bash
git clone <repository-url>
cd ask-soony
uv sync --group dev

cp .env.example .env
cd web && npm ci && npm run build && cd ..
```

Add the secrets and integrations you need to `.env`:

| For | Settings |
| --- | --- |
| Portal download and polling | `SOONY_PORTAL_TOKEN` |
| Default AI analysis | `ANTHROPIC_API_KEY` |
| OpenAI analysis | `ASK_LLM_PROVIDER=openai`, `ASK_LLM_API_KEY` or `OPENAI_API_KEY` |
| Local OpenAI-compatible model | `ASK_LLM_PROVIDER=llamacpp`, `ASK_LLM_BASE_URL` |
| No model calls | `ASK_LLM_PROVIDER=none` |
| Password-protected dashboard | `ASK_DASHBOARD_PASSWORD` |
| Supplier email and replies | `ASK_MAIL_ADDRESS`, `ASK_MAIL_PASSWORD`, `ASK_SMTP_HOST`, `ASK_IMAP_HOST` |

See [`.env.example`](.env.example) for optional push, Teams, call, voice, and alert settings.

### Start the decision inbox

```bash
# UI and API over local state
uv run ask-soony serve --port 8000

# UI, API, portal polling, and pipeline refresh
uv run ask-soony run --port 8000
```

Open <http://127.0.0.1:8000>. The first portal poll establishes a baseline; later changes create events and refresh the relevant checks. Set `ASK_INTERVAL_SECONDS` to change the default five-minute polling interval.

For frontend development:

```bash
scripts/dashboard.sh
```

This starts the API on port 8000 and Vite with hot reload on <http://localhost:5173>.

## Useful commands

```bash
# Fetch the current portal state
uv run ask-soony download

# Poll once, or keep watching and analysing
uv run ask-soony poll
uv run ask-soony watch

# Rebuild decisions from a changed local portal file
uv run ask-soony refresh soony/erp/stock_snapshot.csv

# Inspect findings and event impact
uv run ask-soony findings
uv run ask-soony predict

# Rehearse a new file landing in the portal
uv run ask-soony drop suppliers/<supplier-id>/notices/example.pdf --from ./example.pdf
```

Run `uv run ask-soony --help` for the full command list.

## Built with

- Python, FastAPI, Uvicorn, SQLite, pandas, and document/OCR tooling
- Anthropic, OpenAI, or a local OpenAI-compatible model
- React, TypeScript, Vite, Tailwind CSS, and D3
- Docker for deployment

The main application lives in `src/ask_soony/`; the live UI is in `web/src/`. `web/globe/` is an earlier parked prototype, not the live product.

## Quality checks

```bash
uv run pytest -q
uv run ruff check .
cd web && npm run build
cd web && npm run lint
```

## Deploy

The included `Dockerfile` builds the frontend and starts Lighthouse with portal polling:

```bash
docker build -t lighthouse .
docker run --rm -p 8000:8000 \
  -e SOONY_PORTAL_TOKEN \
  -e ANTHROPIC_API_KEY \
  -e ASK_DASHBOARD_PASSWORD \
  lighthouse
```

Use persistent storage for `data/` in a real deployment, and set `ASK_DASHBOARD_PASSWORD` before exposing the dashboard outside a trusted network.

## More context

[docs/challenge.md](docs/challenge.md) contains the challenge brief. [docs/plan.md](docs/plan.md) and [docs/strategy.md](docs/strategy.md) describe the team’s product direction and verification-first approach. For repository workflow, read [AGENTS.md](AGENTS.md).

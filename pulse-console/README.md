# Console

A hackathon prototype for Lio's **Keep the Line Running** challenge. It turns a buyer's natural-language request into an approval-aware procurement workflow and runs it automatically. The left workspace opens on **Inbox**; **Pipeline** starts blank and shows the diagram only after a request is planned.

## Run

From this folder:

```sh
python -m uvicorn backend.main:app --host 127.0.0.1 --port 4178
```

Open http://localhost:4178. Workflow compilation is deterministic and server-owned. The server loads `ANTHROPIC_API_KEY` from the repository `.env` only for direct natural-language analysis or new event-impact predictions; it never sends the key to the browser.

For live workflow execution, set these values in the repository `.env`:

```sh
SOONY_PORTAL_URL=https://your-soony-portal-base-url
SOONY_PORTAL_TOKEN=your-token
```

`SOONY_PORTAL_URL` is the portal base URL. FastAPI calls its `/api/v1/files` endpoint with `Authorization: Bearer <SOONY_PORTAL_TOKEN>`, mirrors every live file into `console/.pulse-data/portal-mirror`, and records immutable content-addressed versions in `console/.pulse-data/versions`. The browser receives only rendered workflow state and evidence previews.

## Verification and impact intelligence

Console invokes the sibling ask-soony Python source contracts directly: its checksum poller, immutable version store, materializer, verifier, predictor, and decision-card builder. Requests about SAP/confirmation/receipt/stock inconsistencies create a **Verification** node against the materialized live mirror. Requests about notices, news, logistics, shipments or event risk create an **Impact** node against the version store. Neither path uses fixture findings or cached findings.

For local development:

```sh
npm run dev
```

The required ask-soony source is this repository's `src/ask_soony`; set `ASK_SOONY_DIR` only
when using a separate checkout. Install `backend/requirements.txt`. FastAPI runs those Python
modules in-process.
General live-data questions use Anthropic only when
`ANTHROPIC_API_KEY` is configured; the key never leaves the server.

## Conditional workflows

For requests such as “email the supplier only if there is an inconsistency,” Pulse builds a **Condition** node after Verification or Impact. The condition checks for real open findings. When none exist, the pipeline ends quietly: no notification appears in Inbox and no action is attempted.

## Change-triggered workflows

Ask Pulse to “watch for changes” or “run whenever procurement data changes.” It creates a source, preprocessing, and trigger path. Preprocessing stores an indexed baseline of the live files (path, size, SHA-256 content hash, and CSV row count) in `console/.pulse-data/watchers.json`; the folder is git-ignored. The server checks the portal every 30 seconds, runs the downstream agent only after a real change, and re-arms the workflow after it completes.

## Focus

Pulse is designed for Soony's Grand Heist 6 launch: it watches delivery signals, links exceptions to BOM and pack-out exposure, records supplier follow-up actions, and requires approval before an external action by default. A terminal **Notification** node sends the live finding and its source evidence to Inbox; approving it permits the requested action and rejecting it prevents any external action. An email bypasses Inbox only when the request explicitly authorizes that exact recipient without approval and a single verified recipient can be found in the request or live data.

The execution adapter reads the configured live portal. Email is sent only through the configured SMTP mailbox. A spoken briefing is browser speech rather than a telephone call. If either capability is unavailable, the run stops with an error instead of claiming it completed.

## Approved email actions

When a workflow reaches an email action, Pulse extracts recipient addresses from the live portal documents and adds an email approval to Inbox. Clicking **Email** opens an editable draft; sending that draft sends it through the configured SMTP mailbox. It requires `ASK_MAIL_ADDRESS`, `ASK_MAIL_PASSWORD`, and `ASK_SMTP_HOST`.

## Spoken briefings

For a workflow that proposes a call, Inbox shows **Call**. It speaks the verified live finding in the browser, records that the briefing was spoken, and completes the pipeline. It is not a phone call and does not use the microphone. A notification workflow similarly adds an Inbox approval without making an external delivery.

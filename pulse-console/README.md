# Pulse procurement console

A hackathon prototype for Lio's **Keep the Line Running** challenge. It turns a buyer's natural-language request into an approval-aware procurement workflow, shows the workflow build on a node canvas, and runs it automatically.

## Run

From this folder:

```sh
npm run dev
```

Open http://localhost:4178. The server loads `ANTHROPIC_API_KEY` from the repository `.env`; it never sends the key to the browser. If the key or model service is unavailable, workflow planning stops with a visible error.

For live workflow execution, set these values in the repository `.env`:

```sh
SOONY_PORTAL_URL=https://your-soony-portal-base-url
SOONY_PORTAL_TOKEN=your-token
```

`SOONY_PORTAL_URL` is the portal base URL. Pulse calls its `/api/v1/files` endpoint with `Authorization: Bearer <SOONY_PORTAL_TOKEN>`, then downloads relevant JSON, CSV and text documents through `/api/v1/files/<path>`. It verifies that request on load and refuses to run against fixture data.

## Focus

Pulse is designed for Soony's Grand Heist 6 launch: it watches delivery signals, links exceptions to BOM and pack-out exposure, records supplier follow-up actions, and asks for approval before financial or production-impacting action.

The execution adapter reads the configured live portal. A supplier action or approval connector still needs its own API endpoint and credentials; until then those nodes explicitly pause and do not claim to send or change anything externally.

## Approved email actions

When a workflow reaches an email action, Pulse extracts recipient addresses from the live portal documents and creates an approval card. Clicking **Approve & send** sends the displayed draft through the configured SMTP mailbox. It requires `ASK_MAIL_ADDRESS`, `ASK_MAIL_PASSWORD`, and `ASK_SMTP_HOST`.

OpenAI Realtime calls also require Twilio credentials and a public webhook URL. Pulse shows calling as unavailable until those dependencies are configured; it does not simulate calls.

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

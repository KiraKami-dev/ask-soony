# AGENTS.md

Guidance for coding agents (Claude Code, Codex, others) working in this repo.

## What this is

Our entry for the Lio challenge "Keep the Line Running" at START Hack Munich 2026.

- Fictional customer: Soony, a games publisher launching Grand Heist 6 on Sun 22 Nov, 20:00
- Everything is packed in one distribution center in Hamburg; six suppliers deliver the parts
- Still open at kickoff: 128,000 Standard Editions (4 parts each) and 25,200 numbered Collector's Editions (7 parts each)
- Packing time left: Sat 06:00 to 22:00, Sun 06:00 to 20:00

The product idea is not fixed yet. Check the README and recent commits for the current direction.

## The rules that shape the code

- **Agents do real work.** They read, decide and act: write to suppliers, open decisions, escalate. A dashboard on top of a spreadsheet is not enough.
- **Nothing hardcoded.** Never copy values from the data into code. Everything is derived from the portal data at runtime.
- **The data changes.** New files land in the portal during the hack, unannounced. Code must handle new and changed files without a rebuild.
- **The data is messy on purpose.** Expect contradictions, missing values, scans and four languages (KR, CN, PL, EN). Surface contradictions instead of silently picking one side.
- **Confidential.** Use the data only for this hackathon and never share it outside the team. Do not commit portal data (`data/` is gitignored); fetch it through the API.

## Data access

- Customer portal: `https://start-hack-portal-cxh3crbdaef2f8gt.westeurope-01.azurewebsites.net`
- API (team token from the portal's Settings): `GET /api/v1/files`, `GET /api/v1/files/{path}`, `GET /api/v1/archive.zip`, `GET /api/v1/openapi.json`
- Token and other secrets go in `.env` (gitignored), never in code or commits
- Supplier email only gets answers from sender addresses registered in the portal's Settings

## Deadlines

- **Sun 12:00:** hand-in: deployed prototype, pitch deck, demo video (max 3 min), this repo with a short README
- **Sun 14:00:** live demo, 3 min pitch plus 2 min questions

Prefer working, deployed and demoable over complete. Go deep on one problem.

## Entire (session tracking)

Entire records agent sessions as checkpoints and links them to commits.

- Hooks for Claude Code (`.claude/settings.json`) and Codex (`.codex/hooks.json`) are committed; they do nothing if `entire` is not installed
- Checkpoints upload to this (private) repo on `git push`

### Setup (once per clone)

1. Install the CLI. macOS: `brew install --cask entireio/tap/entire`. Other systems: see https://docs.entire.io/cli/installation
2. Check it runs: `entire --version`
3. In your clone, install the git hooks (they link commits to checkpoints and upload them on push):

   ```bash
   entire enable --agent claude-code --local --telemetry=false
   entire agent add codex    # only if you use Codex
   ```

   `--local` writes your choices to `.entire/settings.local.json`, so the shared `.entire/settings.json` stays unchanged. Drop `--telemetry=false` if you don't mind anonymous usage stats.
4. **Restart your agents:** quit every Claude Code or Codex session that was open in this repo and start a new one. Hooks load when a session starts, so sessions that were already running are not tracked
5. Codex only: in the new session, open `/hooks` and approve the Entire hooks
6. Verify: `entire status` should show `Enabled`, your agents, `Checkpoints sync to: origin` and `Secret scanners · betterleaks, goredact`
7. If `entire enable` changed `.claude/settings.json` or `.codex/hooks.json`, check `git diff` before committing; the committed versions already work for everyone

`entire doctor` diagnoses and fixes stuck sessions.
- Shared redaction settings (PII, betterleaks, goredact) live in `.entire/settings.json`; personal settings go in `.entire/settings.local.json` (gitignored)
- Redaction is best effort: never paste secrets into agent sessions

## Conventions

- Commit subjects in imperative mood, at most 50 characters; details as bullets in the body
- One commit per topic
- Keep secrets, tokens and personal data out of code, fixtures and commit messages

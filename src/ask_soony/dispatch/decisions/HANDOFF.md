# Hand-off: outgoing messages

The verifier finds problems and recommends what to do. Sending the messages is owned by colleagues (the email and agent side). This note is what they need.

## Input

`uv run ask-soony refresh <changed-path>` writes `data/state/dispatch.json`. It has one entry per decision:

| Field | Meaning |
|---|---|
| `id`, `title`, `part`, `edition` | What the card is about |
| `at_risk`, `at_risk_if_acted` | Copies at risk now, and if the fix lands in time |
| `act_by`, `runs_dry` | Latest time to act, and when the line runs out of the part |
| `system_says`, `paper_says`, `evidence` | Why |
| `options`, `decision` | What can be done, and what a person must decide |
| `messages[]` | Drafts: `to_role`, `to_email`, `subject`, `body` |

## What the drafts don't do yet

- **Internal email addresses are empty.** The data has names and roles (from the org charts and the policy), but no addresses.
- **Sender:** supplier mailboxes only reply to sender addresses registered in the portal's Settings.
- **Everything is in English.** See translation below.
- **No reply handling.** A supplier's answer should come back in as a new document. The image recognizer and the linker can then read it like any other.

## Known gaps in the JSON and the tools

Open at the time of writing; check the code before relying on them.

- **Edition headline is missing a field.** `editions[]` has `at_risk` but not `at_risk_if_acted`. The HTML page shows both; the JSON doesn't. If you show an edition total, take it from the cards or add the field.
- **The `impact` CLI doesn't print the timing.** `uv run python -m ask_soony.verifier.impact` shows gaps but not `act_by` or the "if acted" numbers. Only the cards (JSON and HTML) carry them.
- **`act_by` can be a guess.** For the cases card (`collect-CMP-GAM-003`) `act_by` equals `runs_dry`, because the port pickup has no stated lead time and is treated as 0. Read it as "act now", not as a deadline.
- **Nothing is sent, and the code is not committed.** The page only offers *Email* (mailto) and *Copy text*, so the drafts above are the input for a real sender. Everything under `verifier/act/`, `verifier/impact/` and `verifier/linker/adapters/planning.py` was still uncommitted at the time of writing.

## Clock: `act_by` is fixed to the stock data

"Now" is the time of the newest stock figures (Sat 21 Nov 20:50), not the machine clock, so the same data always gives the same times. On Sunday at the demo, every `act_by` will be in the past. Until a live-clock mode (mark cards overdue) or a replay option (`--now`) exists, treat `act_by` as "as of the stock data".

## Idea: translate supplier emails into their language

Each supplier profile has `preferred_language` (Karton `pl`, Hwaseong `ko`, Dongguan and PackRight `zh`, Boise and Pune `en`).

- **Option A, small local model:** `facebook/m2m100_418M` from Hugging Face (MIT licence, about 1.9 GB, one model for pl/ko/zh, a few seconds per email on a laptop). It needs PyTorch, which is heavy to deploy, so translate locally and ship the text.
- **Option B, Claude at send time:** better quality, a small cost per email, already in the stack.
- **Either way:** send **bilingual** (English plus the translation), and check that every number, date and part number in the translation matches the English text before sending.

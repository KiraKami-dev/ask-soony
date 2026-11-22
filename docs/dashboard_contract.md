# Dashboard contract

How modules plug into the decision inbox (`scripts/dashboard.sh`). Everything is JSON files; modules never call the API.

## 1. Drop a card file

Write `data/store/<module>_cards.json` (any name matching `*cards*.json`). The API reads all of them on every request, so new files show up within 5 s. Cards merge by `id` (later file name wins, duplicates are logged); each card is tagged with `source_file` and `module` (the file stem). `editions`, `parts`, `now`, `hours_left`, `packing_ends` come from the first file that has them (alphabetically; the verifier's `decision_cards.json`).

`ASK_SOONY_STATE` overrides the folder scan: one path, a glob, or a comma-separated list.

```json
{
  "editions": [], "parts": [],
  "cards": [{
    "id": "stable-unique-id", "title": "...", "edition": "...", "part": "...",
    "at_risk": 0, "at_risk_if_acted": 0, "act_by": "ISO time", "runs_dry": "ISO time|null",
    "stage": "before_arrival | after_dock",
    "system_says": "...", "paper_says": "...",
    "evidence": ["text" | {"doc_path": "", "page": 1, "quote": "", "language": "", "translation": ""}],
    "options": ["text" | {"action": "", "cost": "", "effect": "", "recommended": true}],
    "messages": [{"to_role": "", "to_email": "", "subject": "", "body": ""}],
    "decision": null, "blocked_only": false
  }]
}
```

Keep card ids stable between runs, otherwise a human decision no longer matches its card.

## 2. Read decisions before acting (sender)

`data/state/decisions.json`, keyed by card id, written when a human clicks:

```json
{ "<card_id>": { "action": "allow_once | allow_always | edit | dismiss", "note": null, "ts": "ISO" } }
```

- `allow_once`: send this card's `messages` once. `allow_always`: send, and send future cards of the same kind without asking.
- `edit`: `note` holds the human's changes; do not send until the card is re-issued.
- `dismiss`: never send.
- `data/state/mode.json`: `{"mode": "ask" | "autonomous"}`. In `ask` send nothing without a decision for the card; in `autonomous` send without waiting.

## 3. Write message status back

`data/state/actions.json`, either `{ "<card_id>": {...} }` or a list of records:

```json
{ "card_id": "...", "status": "sent | replied", "at": "ISO", "reply": "optional text" }
```

The API merges the record into the card as `action_status`; the UI shows a SENT or REPLIED badge.

## 4. Activity feed

`/api/feed` merges, newest first, events `{at, actor, text, ref?}` from:

- `data/state/feed.json` (UI clicks; modules may append the same shape),
- the watcher store `data/store/ask-soony.db` (polls with changes, new or changed file versions with their summary when the `changes` table exists, analyses, findings), read-only,
- the mail log: `data/outbox/*.json` (sent) and `data/inbox/*.eml` (received).

Missing files, tables or columns are skipped silently. `ASK_DATA_DIR` moves the store folder.

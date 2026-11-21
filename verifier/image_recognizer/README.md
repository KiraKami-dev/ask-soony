# Image recognizer (discovery run)

Reads every document (scans, photos, PDFs) with Claude through the Claude Agent SDK, using your Claude Code login (no API key). The goal is to see what the documents say and what kinds of errors keep coming up.

```bash
uv run python -m verifier.image_recognizer                       # all ~277 documents, resumable
uv run python -m verifier.image_recognizer --only WZ-261116-473   # one document
uv run python -m verifier.image_recognizer --images-only --limit 5
```

Options: `--model` (default `claude-sonnet-5-5`), `--effort` (default `high`), `--concurrency` (default 4), `--force` (read again).

## Output: `data/store/image_recognizer/` (gitignored, confidential)

One folder per document, `<path>__<sha12>/`:

| File | Content |
|---|---|
| `verdict.json` | Everything on the paper as values: raw text **and** a plain `number` (`80.000` → 80000), printed/handwritten/stamped, crossed out, legible. Plus references, statements, `decisions` (inspection pass/fail, approvals), corrections, and the verdict (status, red flags, observations) |
| `thinking.json` | The model's thinking, when it thinks (summarized; easy documents often get none) |
| `transcript.json` | Every message: tool calls, results and text (image bytes left out) |
| `meta.json` | Checksum, model, time, turns, tokens, cost, status, error |

Plus `index.jsonl` (one line per document read) and `run_summary.json` (counts by type, verdict and status, plus errors).

## Behaviour

- **Resumable:** results are keyed by file checksum. Stop any time, and the next run skips what's done. A changed file is read again.
- **Isolated:** the model gets only the Read tool, none of the project's settings, hooks or CLAUDE.md, and a prompt that treats document text as data.
- **Retries:** 2 retries for API or process errors. Safety blocks aren't retried; they're logged and skipped.

## Not read yet (to do)

These 6 files fail with `CLIJSONDecodeError: JSON message exceeded maximum buffer size of 1048576 bytes`. They are the largest scans, and the SDK's 1 MB per-message limit is too small for them. They were skipped to save Claude usage. Fix: pass `max_buffer_size` (for example 64 MB) to `ClaudeAgentOptions` in `run.py`, then rerun. The buffer size isn't part of the fingerprint, so nothing else gets read again.

- `suppliers/SUP-0028_Karton_Nowak/contracts/SUP-0028_Supply_Agreement_2021.pdf`
- `suppliers/SUP-0019_Dongguan_Precision_Housings/contracts/SUP-0019_Supply_Agreement_2018.pdf`
- `soony/receiving/DC-HAM/20261021_DN-261016-359.png`
- `soony/receiving/DC-HAM/20260907_DN-260902-677.png`
- `soony/receiving/DC-HAM/20261001_WZ-260930-528.png`
- `soony/receiving/DC-HAM/20261106_SN-261001-815.png`

# Image recognizer (discovery run)

Reads every document (scans, photos, PDFs) with one OpenAI vision call each (`OPENAI_API_KEY` from `.env`). Images are resized and PDF pages without a text layer are rendered to images by the shared `ask_soony.extract`; the answer is forced into the JSON schema in `schema.py`. The goal is to see what the documents say and what kinds of errors keep coming up.

```bash
uv run python -m verifier.image_recognizer                       # all ~277 documents, resumable
uv run python -m verifier.image_recognizer --only WZ-261116-473   # one document
uv run python -m verifier.image_recognizer --images-only --limit 5
```

Options: `--model` (default `gpt-5.4-mini`), `--effort` (default `medium`), `--concurrency` (default 4), `--force` (read again).

## Output: `data/store/image_recognizer/` (gitignored, confidential)

One folder per document, `<path>__<sha12>/`:

| File | Content |
|---|---|
| `verdict.json` | Everything on the paper as values: raw text **and** a plain `number` (`80.000` → 80000), printed/handwritten/stamped, crossed out, legible. Plus references, statements, `decisions` (inspection pass/fail, approvals), corrections, and the verdict (status, red flags, observations) |
| `meta.json` | Checksum, model, time, token usage, status, error |

Plus `index.jsonl` (one line per document read) and `run_summary.json` (counts by type, verdict and status, plus errors).

## Behaviour

- **Resumable:** results are keyed by file checksum. Stop any time, and the next run skips what's done. A changed file is read again.
- **Isolated:** one call, no tools; the prompt treats document text as data.
- **Retries:** 2 retries for API or process errors. Refusals and schema failures aren't retried; they're logged and skipped.

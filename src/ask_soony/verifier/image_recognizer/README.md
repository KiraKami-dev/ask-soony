# Image recognizer (discovery run)

Reads every document (scans, photos, PDFs) with one OpenAI vision call each (`OPENAI_API_KEY` from `.env`). Images are resized and PDF pages without a text layer are rendered to images by the shared `ask_soony.extract`; the answer is forced into the JSON schema in `schema.py`. The goal is to see what the documents say and what kinds of errors keep coming up.

```bash
uv run python -m ask_soony.verifier.image_recognizer                       # all ~277 documents, resumable
uv run python -m ask_soony.verifier.image_recognizer --only WZ-261116-473   # one document
uv run python -m ask_soony.verifier.image_recognizer --images-only --limit 5
```

Options: `--model` (default `gpt-5.4-mini`), `--effort` (default `medium`), `--concurrency` (default 4), `--force` (read again).

## Baseline and gate

- **Baseline:** the first full run (`claude-sonnet-5-5`, 283 documents) is frozen in `src/ask_soony/verifier/baseline_sonnet/`. A run never reads a file version the baseline has (`--ignore-baseline` overrides). For the same file version the baseline wins over the live store; live readings only fill versions it lacks. It only grows: `python -m ask_soony.verifier.baseline add <store>` copies readings of versions it lacks (verdict and meta only).
- **Ignored:** `src/ask_soony/verifier/ignored_documents.json` lists documents that are never read, each with the reason. Today only the Hwaseong LTA Amendment 3 redline (its meaning is in the strike-through, which the text layer loses).
- **Gate:** before trusting a new model or prompt, score it on the must-catch documents in `golden.json` (handwritten correction, failed lot, short shipment, ...) plus clean notes as negative controls (no invented corrections). Facts are compared after the linker's fixes, not by field name.

```bash
uv run python -m ask_soony.verifier.image_recognizer.gate --model gpt-5.4-mini   # reads 8 docs into data/store/gate/
uv run python -m ask_soony.verifier.image_recognizer.gate --readings src/ask_soony/verifier/baseline_sonnet   # free
```

Exit code 0 only if every must-catch case passes.

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

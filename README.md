# ask-soony: the system says fine, the paper says otherwise

Our entry for the **Lio challenge "Keep the Line Running"** (START Hack Munich 2026).

Soony launches Grand Heist 6 on **Sunday 20:00**. Its launch plan says every part is **ok**. We read every delivery note, confirmation, inspection report and contract, scans and four languages included, and checked the system against the paper. On the same data:

| | Launch plan | Paper trail |
|---|---|---|
| Standard Edition | ok | **32,000 copies at risk**: only 6,000 clear cases in stock, while 350,000 sit uncollected at the Hamburg port |
| Collector's Edition | ok | **5,900 at risk**: SAP booked 80,000 art books, and the delivery note says 8,000 (handwritten correction) |
| Game cards | ok | **6,800 short**: only 28,000 of 48,000 flew |
| If everyone acts in time | | **only the 6,800 game cards remain at risk** |

Every number comes with its evidence, a deadline ("order by Sat 22:16") and who has to act.

## How it works

```
portal data ──► linker ──► image recognizer ──► impact ──► act
(SAP, Excel,    connect     read every scan     what's      decision cards:
 PDFs, scans)   the records and PDF with Claude really      who, what, by when
                and find    (claims + verdict)  missing,
                the gaps                        by when
```

1. **Linker** (`verifier/linker/`): connects every record about the same order, delivery or lot (PO → confirmation → shipment → delivery note → receipt → stock → inspection), and lists what doesn't fit.
2. **Image recognizer** (`verifier/image_recognizer/`): Claude reads each document once and records what's on the paper, including handwriting, crossed-out values and stamps.
3. **Impact** (`verifier/impact/`):
   - **Usable stock:** book stock minus everything the evidence says isn't real or usable.
   - **Gap:** usable stock against what's still to pack, from the BOM.
   - **Run-out time:** when each part runs out at the packing rate.
4. **Act** (`verifier/act/`): one decision card per cause, with *system says* vs *paper says*, the options, the decision needed, and a ready message to the right person. People and roles are read from the org chart and the approval policy. A person decides; nothing is sent on its own.

## Run it

```bash
uv sync --group dev
cp .env.example .env                    # put SOONY_PORTAL_TOKEN in it (portal → Settings)
uv run ask-soony download               # fetch the portal data into data/portal/
```

| Step | Command | Output |
|---|---|---|
| Read the documents (Claude, via the Agent SDK) | `uv run python -m verifier.image_recognizer` | `data/store/image_recognizer/` (resumable, one read per file version) |
| Link and check | `uv run python -m verifier.linker build` | Issues, live ones first, soonest first |
| Follow one order | `uv run python -m verifier.linker trace 4500318768` | The whole chain of that PO, with its issues |
| What's really missing | `uv run python -m verifier.impact` | Need, book stock, usable, gap per part |
| Decision cards | `uv run python -m verifier.act && open data/store/decision_cards.html` | The page, plus `decision_cards.json` for other agents |
| Tests | `uv run pytest` | 127 tests on made-up data |

**The image recognizer uses your Claude Code login.** No API key is needed. Its results are cached by checksum, so the other steps run without any model calls.

## What makes it hold up

- **Nothing hardcoded:**
  - Files are recognised by their columns or tables, so a new drop needs no code change.
  - Supplier names, part mappings, role holders and lead times are all read from the data.
- **Contradictions are shown, not resolved silently:**
  - When two sources disagree, both versions are kept and the difference is named.
  - Link certainty is labelled: `exact` vs `po_level`.
- **Reader mistakes are fixed in code:** for example "19.000" is 19,000, and signatures aren't treated as corrections. The raw text is always kept as evidence.
- **Deterministic:** "now" is the time of the newest stock figures, so the same data always gives the same answer.
- **A human decides:** the system recommends and drafts; concessions and allocations go to the role the policy names.

## More

| Topic | Where |
|---|---|
| Verifier design: evidence rules, routing, linking keys | [verifier/README.md](verifier/README.md) |
| Linker build order and principles | [verifier/linker/linker_strategy.md](verifier/linker/linker_strategy.md) |
| What the document reading found | [verifier/image_recognizer/findings.md](verifier/image_recognizer/findings.md) |
| Hand-off for sending messages | [verifier/act/HANDOFF.md](verifier/act/HANDOFF.md) |
| Product strategy | [strategy.md](strategy.md) |
| Data watcher (polls the portal, keeps every version) | [docs/INSTRUCTIONS.md](docs/INSTRUCTIONS.md#data-watcher-srcask_soony) |
| Team setup and conventions | [docs/INSTRUCTIONS.md](docs/INSTRUCTIONS.md), [AGENTS.md](AGENTS.md) |

Soony and all its data are fictional, from the challenge. The data is used only for this hackathon.

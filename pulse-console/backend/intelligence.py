"""Direct adapters for the vendored ask-soony intelligence modules."""

from __future__ import annotations

import sys
from pathlib import Path

from .config import SIBLING, portal_root, readings_root, state_root, store_root

ASK_SOURCE = SIBLING / "src"
if not (ASK_SOURCE / "ask_soony").is_dir():
    raise RuntimeError(f"ask-soony source is required at {ASK_SOURCE}")
sys.path.insert(0, str(ASK_SOURCE))


def verify() -> dict:
    from ask_soony.dispatch.decisions import to_payload
    from ask_soony.dispatch.decisions.cards import build_cards
    from ask_soony.verifier.impact.shortfall import compute
    from ask_soony.verifier.linker import build

    root = portal_root()
    if not root.is_dir():
        raise RuntimeError(f"Verification mirror is unavailable: {root}")
    graph = build(root, readings_root() if readings_root().is_dir() else None)
    result = compute(graph.records, graph.issues)
    return to_payload(result, build_cards(result, graph.issues, graph.records)) | {
        "provenance": {
            "discovered_files": int(len(graph.sources)),
            "records": int(len(graph.records)),
            "issues": int(len(graph.issues)),
        }
    }


def predict(paths: list[str] | None = None, *, force: bool = True) -> list[dict]:
    from ask_soony.llm import make_backend
    from ask_soony.predictor import ImpactAgent
    from ask_soony.store import Store
    import os

    provider = os.environ.get("ASK_LLM_PROVIDER", "anthropic")
    model = os.environ.get("ASK_LLM_MODEL", "claude-sonnet-5-5")
    key = os.environ.get("ANTHROPIC_API_KEY") if provider == "anthropic" else os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Predictor requires the configured LLM API key.")
    store = Store(store_root())
    try:
        backend = make_backend(provider, model, key, os.environ.get("ASK_LLM_BASE_URL", ""), os.environ.get("ASK_LLM_EFFORT") or "medium")
        agent = ImpactAgent(store, backend, state_root())
        events = agent.events_for_paths(paths) if paths else agent.events_from_portal()
        # The caller supplies only changed portal paths. ask-soony's dispatch
        # contract intentionally re-evaluates that exact event version.
        return agent.run(events, force=force)
    finally:
        store.db.close()

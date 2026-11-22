"""Python workflow runtime for Console.

The browser only renders state returned by this module.  It deliberately owns the
portal mirror, workflow planning, execution, approvals, and polling so no server
side Node process (or browser credential) is required.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import smtplib
import sys
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import CONSOLE, SIBLING, load_env, setting
from .intelligence import predict, verify

ASK_SOURCE = SIBLING / "src"
if not (ASK_SOURCE / "ask_soony").is_dir():
    raise RuntimeError(f"ask-soony source is required at {ASK_SOURCE}")
sys.path.insert(0, str(ASK_SOURCE))

NODE_TYPES = {"source", "preprocess", "trigger", "verification", "impact", "agent", "condition", "action", "notification", "output"}
TEXT_EXTENSIONS = {".json", ".csv", ".txt", ".md"}
RELEVANT = re.compile(r"(erp|shipment|stock|purchase|goods|pack|supplier|contract|notice|launch)", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


class WorkflowError(RuntimeError):
    pass


def now() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat(timespec="seconds")


def data_root() -> Path:
    return Path(setting("PULSE_WORKFLOW_DATA") or CONSOLE / ".pulse-data")


def mirror_root() -> Path:
    return data_root() / "portal-mirror"


def watcher_file() -> Path:
    return data_root() / "watchers.json"


def _safe_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise WorkflowError("The portal returned an unsafe file path.")
    return candidate


def _portal_config() -> tuple[str, str]:
    raw_url = setting("SOONY_PORTAL_URL") or setting("URL")
    token = setting("SOONY_PORTAL_TOKEN") or setting("TOKEN")
    if not raw_url or not token:
        raise WorkflowError("SOONY_PORTAL_URL and SOONY_PORTAL_TOKEN are required to run a workflow.")
    return (raw_url if raw_url.startswith(("http://", "https://")) else f"https://{raw_url}").rstrip("/"), token


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def sync_portal() -> dict[str, Any]:
    """Run ask-soony's real poll contract and materialize its current version store."""
    load_env()
    base, token = _portal_config()
    from ask_soony.portal import PortalClient
    from ask_soony.store import Store
    from ask_soony.watcher import mirror, poll

    store = Store(data_root() / "versions")
    client = PortalClient(base, token)
    try:
        # This is ask-soony.watcher.poll: download missing immutable blobs,
        # record the checksum-versioned listing, and compute change summaries.
        poll_result = poll(store, client)
        root = mirror_root().resolve()
        mirror(store, root)
        current: dict[str, dict[str, Any]] = {}
        documents: list[dict[str, Any]] = []
        for path, version in store.current().items():
            rel = _safe_path(path)
            target = (root / rel).resolve()
            if not target.is_relative_to(root):
                raise WorkflowError(f"The portal returned an unsafe file path: {path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            content = store.read_blob(version.sha256)
            if not target.exists() or target.read_bytes() != content:
                target.write_bytes(content)
            current[path] = {"sha256": version.sha256, "size": version.size}
            if target.suffix.lower() in TEXT_EXTENSIONS and RELEVANT.search(path):
                documents.append({"path": path, "bytes": version.size, "text": _decode(content)})
        return {
            "fileCount": len(current),
            "documents": documents,
            "manifest": current,
            "poll": {"baseline": poll_result.baseline, "changed": [item.path for item in [*poll_result.new, *poll_result.changed]], "removed": poll_result.removed},
        }
    except Exception as error:
        raise WorkflowError(f"Live portal poll failed: {error}") from error
    finally:
        client._http.close()
        store.db.close()


def health() -> dict[str, Any]:
    try:
        portal = sync_portal()
        return {"portalConnected": True, "summary": f"{portal['fileCount']} portal files mirrored"}
    except WorkflowError as error:
        return {"portalConnected": False, "error": str(error), "summary": "Portal mirror unavailable"}


def requested_modules(request: str) -> dict[str, bool]:
    return {
        "verification": bool(re.search(r"\b(inconsisten\w*|mismatch\w*|verif\w*|reconcil\w*|compar\w*|sap|confirmation\w*|receipt\w*|inventory|stock|purchase orders?)\b", request, re.I)),
        "impact": bool(re.search(r"\b(news|notice\w*|logistics|shipment\w*|forecast\w*|predict\w*|event risk|external event|disruption\w*|port delay|supplier alert)\b", request, re.I)),
    }


def compile_workflow(request: str) -> dict[str, Any]:
    """Compile a request into the fixed, approval-safe Console pipeline."""
    asks_email = bool(re.search(r"\b(email|send .*mail|mail .*supplier)\b", request, re.I))
    asks_call = bool(re.search(r"\b(call|phone|dial)\b", request, re.I))
    asks_notice = bool(re.search(r"\b(notif(?:y|ication)|inbox|approval)\b", request, re.I))
    needs = requested_modules(request)
    nodes: list[dict[str, Any]] = [{"id": "source", "label": "Soony portal", "type": "source"}]
    if re.search(r"\b(watch|trigger|whenever|every time|when .*changes?|monitor .*changes?)\b", request, re.I):
        nodes += [{"id": "index", "label": "Index live data", "type": "preprocess"}, {"id": "trigger", "label": "Wait for data change", "type": "trigger"}]
    if needs["verification"]:
        nodes.append({"id": "verification", "label": "Verify live records", "type": "verification"})
    if needs["impact"]:
        nodes.append({"id": "impact", "label": "Predict event impact", "type": "impact"})
    if not any(needs.values()):
        nodes.append({"id": "agent", "label": "Analyse live procurement data", "type": "agent"})
    conditional = bool(re.search(r"\b(if|when|only if)\b", request, re.I)) and (asks_email or asks_call or asks_notice)
    if conditional and any(needs.values()):
        nodes.append({"id": "condition", "label": "If findings exist", "type": "condition", "rule": "has_findings"})
    if asks_email or asks_call or asks_notice:
        nodes.append({"id": "notification", "label": "Live finding", "type": "notification", "proposedAction": "call" if asks_call else "email" if asks_email else "notification"})
    else:
        nodes.append({"id": "outcome", "label": "Live result", "type": "output"})
    return {"summary": "Procurement workflow", "message": "I prepared the workflow and will run it against the live procurement data.", "confidence": 0, "impact": "Live assessment pending", "nodes": nodes[:7], "actions": []}


def plan(request: str, history: list[dict[str, Any]]) -> dict[str, Any]:
    # This is intentionally deterministic: LLMs do not get to decide whether a
    # live version is polled, a specialist module is invoked, or an approval is
    # required. `history` remains part of the API contract for the browser chat.
    del history
    return compile_workflow(request)


def _assessment_from_verification() -> dict[str, Any]:
    # The verifier operates over the exact materialized portal version from this poll.
    os.environ["PULSE_INTELLIGENCE_PORTAL_ROOT"] = str(mirror_root())
    payload = verify()
    cards = []
    for card in payload.get("decisions", []):
        messages = [message for message in card.get("messages", []) if isinstance(message, dict)]
        recipient = next((message for message in messages if message.get("to_email")), None)
        cards.append({"id": card.get("id"), "headline": card.get("title"), "summary": card.get("decision") or card.get("paper_says"), "evidence": [{"source": "System says", "value": card.get("system_says", "")}, {"source": "Paper says", "value": card.get("paper_says", "")}], "severity": "high" if card.get("at_risk") else "medium", "messages": messages, "operation": "email" if recipient else "notification", "recipientLabel": (recipient or messages[0]).get("to_role", "") if messages else ""})
    finding = "\n".join(filter(None, [" — ".join(filter(None, [c["headline"], c["summary"]])) for c in cards[:3]])) or "No open verification mismatch was found."
    return {"finding": finding, "cards": cards, "evidence": [e for c in cards for e in c["evidence"] if e["value"]][:4]}


def _assessment_from_impact(paths: list[str] | None = None) -> dict[str, Any]:
    os.environ["PULSE_INTELLIGENCE_STORE"] = str(data_root() / "versions")
    results = predict(paths)
    cards = [{"id": p.get("event_id"), "headline": p.get("event_summary"), "summary": p.get("recommendation"), "severity": "high", "evidence": [{"source": a.get("supplier_id") or p.get("source", "Event"), "value": a.get("evidence") or a.get("mechanism", "")} for a in p.get("affected", [])]} for p in results if p.get("overall_launch_risk") == "yes"]
    finding = "\n".join(filter(None, [" — ".join(filter(None, [c["headline"], c["summary"]])) for c in cards[:3]])) or "No open event-impact risk was found."
    return {"finding": finding, "cards": cards, "evidence": [e for c in cards for e in c["evidence"] if e["value"]][:4]}


def _agent_assessment(request: str, portal: dict[str, Any]) -> dict[str, Any]:
    # Never fabricate an answer: without a configured model the direct question
    # explicitly stops, while verifier/predictor workflows remain fully local.
    if not setting("ANTHROPIC_API_KEY"):
        raise WorkflowError("ANTHROPIC_API_KEY is required for live agent analysis.")
    try:
        import anthropic
        tool = {"name": "report_live_finding", "description": "Return the answer using only supplied portal data.", "input_schema": {"type": "object", "additionalProperties": False, "required": ["finding"], "properties": {"finding": {"type": "string"}, "evidence": {"type": "array", "items": {"type": "object", "properties": {"source": {"type": "string"}, "value": {"type": "string"}}, "required": ["source", "value"]}}}}}
        documents = [{"path": item["path"], "text": item["text"][:12000]} for item in portal["documents"]]
        response = anthropic.Anthropic(api_key=setting("ANTHROPIC_API_KEY")).messages.create(model=setting("ANTHROPIC_MODEL", "claude-sonnet-5-5"), max_tokens=800, system="Answer only from the supplied live portal documents. Call report_live_finding exactly once. Do not invent facts, contacts, or actions. Keep the finding concise and cite relevant path/value evidence.", tools=[tool], messages=[{"role": "user", "content": f"Request: {request}\n\nPortal documents:\n{json.dumps(documents)}"}])
        block = next((item for item in response.content if item.type == "tool_use" and item.name == "report_live_finding"), None)
        if not block or not isinstance(block.input.get("finding"), str) or not block.input["finding"].strip():
            raise WorkflowError("Live analysis did not return a finding.")
        evidence = [item for item in block.input.get("evidence", []) if isinstance(item, dict) and isinstance(item.get("source"), str) and isinstance(item.get("value"), str)]
        return {"finding": block.input["finding"].strip(), "cards": [], "evidence": evidence[:4], "visuals": []}
    except WorkflowError:
        raise
    except Exception as error:
        raise WorkflowError(f"Live analysis failed: {error}") from error


def _emails(portal: dict[str, Any]) -> list[str]:
    return sorted({match.lower() for document in portal["documents"] for match in EMAIL.findall(document.get("text", ""))})[:12]


def _event(node: dict[str, Any], status: str, detail: str, assessment: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {"node": node["label"], "type": node["type"], "status": status, "detail": detail}
    if assessment is not None:
        result["assessment"] = assessment
    return result


def _approval(run: dict[str, Any], node: dict[str, Any], portal: dict[str, Any], card: dict[str, Any] | None = None) -> dict[str, Any]:
    selected = next((message for message in (card or {}).get("messages", []) if message.get("to_email")), None)
    wants_call = node.get("proposedAction") == "call"
    high_risk = (card or {}).get("severity") in {"critical", "high"}
    operation = "call" if wants_call and high_risk and not selected else (card or {}).get("operation") or (node.get("proposedAction", "notification") if node["type"] == "notification" else node.get("operation", "email"))
    finding = " — ".join(filter(None, [(card or {}).get("headline"), (card or {}).get("summary")]))
    body = selected.get("body") if selected else f"Hello,\n\n{finding or run['request']}\n\nRegards,\nSoony Procurement"
    subject = selected.get("subject") if selected else f"Soony procurement: {(card or {}).get('headline') or node['label']}"
    recipients = [selected["to_email"].lower()] if operation == "email" and selected else []
    return {"id": f"approval_{uuid4().hex[:10]}", "node": node["label"], "actionNode": node["label"], "operation": operation, "recipientLabel": (card or {}).get("recipientLabel", ""), "recipients": recipients, "subject": subject, "body": body, "remaining": [], "finding": finding, "issueTitle": (card or {}).get("headline", ""), "evidence": (card or {}).get("evidence", []), "riskLevel": "urgent" if (card or {}).get("severity") in {"critical", "high"} else "attention"}


class Runtime:
    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.watchers: dict[str, dict[str, Any]] = {}
        self.notified = self._load_notification_ledger()
        self._load_watchers()

    @staticmethod
    def _ledger_file() -> Path:
        return data_root() / "inbox-ledger.json"

    def _load_notification_ledger(self) -> dict[str, str]:
        try:
            data = json.loads(self._ledger_file().read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_notification_ledger(self) -> None:
        self._ledger_file().parent.mkdir(parents=True, exist_ok=True)
        self._ledger_file().write_text(json.dumps(self.notified, indent=2))

    def _load_watchers(self) -> None:
        try:
            for item in json.loads(watcher_file().read_text()).get("watchers", []):
                if item.get("run", {}).get("id"):
                    self.watchers[item["run"]["id"]] = item
                    self.runs[item["run"]["id"]] = item["run"]
        except FileNotFoundError:
            pass
        except (ValueError, OSError):
            pass

    def _save_watchers(self) -> None:
        watcher_file().parent.mkdir(parents=True, exist_ok=True)
        temporary = watcher_file().with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "watchers": list(self.watchers.values())}, indent=2))
        temporary.replace(watcher_file())

    @staticmethod
    def _index(portal: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"path": path, "sha256": item.get("sha256"), "size": item.get("size")} for path, item in portal["manifest"].items()]

    def execute(self, workflow: dict[str, Any], request: str) -> dict[str, Any]:
        nodes = workflow.get("nodes", [])
        if not isinstance(nodes, list) or not nodes:
            raise WorkflowError("A workflow with steps is required.")
        portal = sync_portal()
        run = {"id": f"run_{int(time.time() * 1000)}_{uuid4().hex[:5]}", "createdAt": now(), "request": request, "workflow": workflow, "status": "running", "events": [], "activeAssessments": [], "sources": [{"path": d["path"], "bytes": d.get("bytes"), "preview": d.get("text", "")[:500]} for d in portal["documents"]]}
        self._advance(run, nodes, portal)
        self.runs[run["id"]] = run
        return run

    def _advance(self, run: dict[str, Any], nodes: list[dict[str, Any]], portal: dict[str, Any]) -> None:
        for node in nodes:
            kind = node["type"]
            if kind == "source":
                run["events"].append(_event(node, "completed", f"Polled and mirrored {portal['fileCount']} live portal files."))
            elif kind == "preprocess":
                run["baseline"] = self._index(portal)
                run["events"].append(_event(node, "completed", f"Indexed {len(run['baseline'])} live files for change tracking."))
            elif kind == "trigger":
                run["watchMode"] = True
                run["baseline"] = run.get("baseline") or self._index(portal)
                run["events"].append(_event(node, "waiting", "Baseline stored. Waiting for a real portal change before running checks."))
                self._finish(run, portal)
                return
            elif kind == "verification":
                assessment = _assessment_from_verification()
                run["activeAssessments"].append(assessment)
                run["events"].append(_event(node, "completed", assessment["finding"], assessment))
            elif kind == "impact":
                from ask_soony.predictor import is_predictor_path
                changed = run.get("changedPaths")
                if changed is not None and not any(is_predictor_path(path) for path in changed):
                    assessment = {"finding": "Skipped: this change is not an event or logistics input for the Impact module.", "cards": [], "evidence": []}
                else:
                    assessment = _assessment_from_impact(changed)
                run["activeAssessments"].append(assessment)
                run["events"].append(_event(node, "completed", assessment["finding"], assessment))
            elif kind == "agent":
                assessment = _agent_assessment(run["request"], portal)
                run["activeAssessments"].append(assessment)
                run["events"].append(_event(node, "completed", assessment["finding"], assessment))
            elif kind == "condition":
                cards = [card for assessment in run["activeAssessments"] for card in assessment.get("cards", [])]
                if not cards:
                    run["events"].append(_event(node, "completed", "No live findings matched the condition. No notification or action was created."))
                    self._finish(run, portal)
                    return
                run["assessment"] = {"cards": cards}
                run["events"].append(_event(node, "completed", f"{len(cards)} live findings matched the condition."))
            elif kind == "notification":
                cards = [card for assessment in run["activeAssessments"] for card in assessment.get("cards", [])]
                if cards:
                    new_cards = []
                    for card in cards:
                        fingerprint = hashlib.sha256(json.dumps(card, sort_keys=True, default=str).encode()).hexdigest()
                        if self.notified.get(str(card.get("id"))) == fingerprint:
                            continue
                        self.notified[str(card.get("id"))] = fingerprint
                        new_cards.append(card)
                    self._save_notification_ledger()
                    for card in new_cards:
                        issue = {**run, "id": f"{run['id']}_issue_{uuid4().hex[:5]}", "parentRunId": run["id"], "events": list(run["events"]), "status": "waiting_for_approval"}
                        issue["approval"] = _approval(issue, node, portal, card)
                        issue["events"].append(_event(node, "waiting", "A live finding is ready in Inbox."))
                        self.runs[issue["id"]] = issue
                    detail = f"Added {len(new_cards)} separate Inbox notifications." if new_cards else "No Inbox callback: every current finding already has an unchanged notification."
                    run["events"].append(_event(node, "completed", detail))
                else:
                    run["approval"] = _approval(run, node, portal)
                    run["events"].append(_event(node, "waiting", "A live notification is ready in Inbox."))
                    run["status"] = "waiting_for_approval"
                    return
            elif kind == "output":
                run["events"].append(_event(node, "completed", "Live-data run completed."))
        self._finish(run, portal)

    def _finish(self, run: dict[str, Any], portal: dict[str, Any]) -> None:
        if run.get("watchMode"):
            run["status"] = "waiting_for_trigger"
            self.watchers[run["id"]] = {"run": run, "baseline": run.get("baseline") or self._index(portal)}
            self._save_watchers()
        else:
            run["status"] = "completed"

    def inbox(self) -> list[dict[str, Any]]:
        return [{key: run[key] for key in ("id", "createdAt", "approval", "events", "sources", "status") if key in run} for run in self.runs.values() if run.get("status") == "waiting_for_approval" and run.get("approval")]

    def reject(self, run_id: str) -> dict[str, Any]:
        run = self.get(run_id)
        if not run.get("approval") or run.get("status") != "waiting_for_approval":
            raise WorkflowError("This Inbox item is no longer pending.")
        approval = run.pop("approval")
        run["events"].append({"node": approval["node"], "type": "notification", "status": "rejected", "detail": "Rejected in Inbox. No external action was taken."})
        run["status"] = "rejected"
        return run

    def say_complete(self, run_id: str) -> dict[str, Any]:
        run = self.get(run_id)
        approval = run.get("approval")
        if not approval or approval.get("operation") != "call":
            raise WorkflowError("This spoken briefing is no longer pending.")
        run.pop("approval")
        run["events"].append({"node": approval["node"], "type": "notification", "status": "completed", "detail": "Live procurement finding spoken in the browser."})
        run["status"] = "completed"
        return run

    def approve(self, run_id: str, recipient: str = "", subject: str = "", body: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
        run = self.get(run_id)
        approval = run.get("approval")
        if not approval or run.get("status") != "waiting_for_approval":
            raise WorkflowError("This approval is no longer pending.")
        if approval["operation"] == "call":
            raise WorkflowError("Use the spoken briefing control to complete this action.")
        action: dict[str, Any] = {"type": "notification"}
        if approval["operation"] == "email":
            recipient = recipient.lower()
            if recipient not in approval["recipients"]:
                raise WorkflowError("Choose an email address offered for this approval.")
            self._send_email(recipient, subject or approval["subject"], body or approval["body"])
            action = {"type": "email", "sent": {"to": recipient, "subject": subject or approval["subject"]}}
        run.pop("approval")
        run["events"].append({"node": approval["node"], "type": "notification", "status": "completed", "detail": "Inbox approval confirmed." if action["type"] == "notification" else f"Email sent to {recipient}."})
        run["status"] = "completed"
        return run, action

    @staticmethod
    def _send_email(recipient: str, subject: str, body: str) -> None:
        address, password, host = setting("ASK_MAIL_ADDRESS"), setting("ASK_MAIL_PASSWORD").replace(" ", ""), setting("ASK_SMTP_HOST")
        if not all((address, password, host)):
            raise WorkflowError("Email requires ASK_MAIL_ADDRESS, ASK_MAIL_PASSWORD, and ASK_SMTP_HOST.")
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = address, recipient, subject.replace("\n", " ")
        message.set_content(body)
        with smtplib.SMTP(host, 587, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(address, password)
            smtp.send_message(message)

    def get(self, run_id: str) -> dict[str, Any]:
        if run_id not in self.runs:
            raise WorkflowError("Workflow run not found.")
        return self.runs[run_id]

    def document(self, run_id: str, path: str) -> dict[str, str]:
        run = self.get(run_id)
        if path not in {item.get("path") for item in run.get("sources", [])}:
            raise WorkflowError("This document was not used by the workflow run.")
        local = mirror_root() / _safe_path(path)
        if not local.exists():
            raise WorkflowError("The mirrored document is no longer available.")
        return {"path": path, "text": _decode(local.read_bytes())}

    def check_watchers(self) -> None:
        active = []
        for run_id, record in list(self.watchers.items()):
            if record["run"].get("status") == "waiting_for_trigger":
                active.append((run_id, record))
            else:
                self.watchers.pop(run_id, None)
        if not active:
            self._save_watchers()
            return
        try:
            # Poll exactly once, then fan that one immutable poll result out to
            # every interested workflow. Polling once per watcher would let the
            # first workflow consume the changed-version signal.
            portal = sync_portal()
        except WorkflowError:
            return
        changed = [*portal["poll"]["changed"], *portal["poll"]["removed"]]
        if not changed:
            return
        for run_id, record in active:
            run = record["run"]
            self.watchers.pop(run_id, None)
            run["changedPaths"], run["status"], run["activeAssessments"], run["baseline"] = changed, "running", [], self._index(portal)
            trigger = next((node for node in run["workflow"]["nodes"] if node["type"] == "trigger"), {"label": "Data change trigger", "type": "trigger"})
            run["events"].append(_event(trigger, "completed", f"Detected {len(changed)} live portal change(s). Running downstream checks."))
            index = next(i for i, node in enumerate(run["workflow"]["nodes"]) if node["type"] == "trigger")
            self._advance(run, run["workflow"]["nodes"][index + 1 :], portal)
        self._save_watchers()


runtime = Runtime()


async def watcher_loop() -> None:
    while True:
        await asyncio.sleep(30)
        await asyncio.to_thread(runtime.check_watchers)

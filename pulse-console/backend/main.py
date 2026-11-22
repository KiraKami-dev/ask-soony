"""Console's Python-only HTTP boundary."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import os

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import CONSOLE, load_env
from .workflow import WorkflowError, health, plan, runtime, watcher_loop


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_env()
    task = asyncio.create_task(watcher_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Console", lifespan=lifespan)


def fail(error: Exception, status: int = 422) -> None:
    raise HTTPException(status, str(error)) from error


@app.get("/api/health")
def api_health() -> dict:
    return health()


@app.get("/api/capabilities")
def capabilities() -> dict:
    return {"email": bool(os.environ.get("ASK_MAIL_ADDRESS") and os.environ.get("ASK_MAIL_PASSWORD") and os.environ.get("ASK_SMTP_HOST"))}


@app.get("/api/models")
def models() -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        fail(WorkflowError("ANTHROPIC_API_KEY is required."), 502)
    try:
        response = httpx.get("https://api.anthropic.com/v1/models?limit=100", headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=15)
        response.raise_for_status()
        return {"models": [item["id"] for item in response.json().get("data", []) if isinstance(item.get("id"), str)]}
    except httpx.HTTPError as error:
        fail(WorkflowError(f"Could not list Claude models: {error}"), 502)


@app.post("/api/agent")
def agent(payload: dict) -> dict:
    request = str(payload.get("message", "")).strip()
    if not request:
        fail(WorkflowError("A workflow request is required."), 400)
    try:
        return plan(request, payload.get("history") if isinstance(payload.get("history"), list) else [])
    except WorkflowError as error:
        fail(error, 502)


@app.post("/api/run")
def run(payload: dict) -> dict:
    try:
        workflow = payload.get("workflow") if isinstance(payload.get("workflow"), dict) else {}
        return runtime.execute(workflow, str(payload.get("request", "Procurement workflow")))
    except WorkflowError as error:
        fail(error, 400)
    except Exception as error:
        fail(WorkflowError(f"Could not run workflow: {error}"), 422)


@app.get("/api/inbox")
def inbox() -> dict:
    return {"items": runtime.inbox()}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    try:
        return runtime.get(run_id)
    except WorkflowError as error:
        fail(error, 404)


@app.get("/api/runs/{run_id}/document")
def document(run_id: str, path: str) -> dict:
    try:
        return runtime.document(run_id, path)
    except WorkflowError as error:
        fail(error, 403 if "not used" in str(error) else 404)


@app.post("/api/runs/{run_id}/say-complete")
def say_complete(run_id: str) -> dict:
    try:
        run = runtime.say_complete(run_id)
        return {"run": run, "detail": "Live procurement finding spoken in the browser."}
    except WorkflowError as error:
        fail(error)


@app.post("/api/reject")
def reject(payload: dict) -> dict:
    try:
        return {"run": runtime.reject(str(payload.get("runId", "")))}
    except WorkflowError as error:
        fail(error)


@app.post("/api/approve")
def approve(payload: dict) -> dict:
    try:
        run, action = runtime.approve(str(payload.get("runId", "")), str(payload.get("recipient", "")), str(payload.get("subject", "")), str(payload.get("body", "")))
        return {"run": run, "action": action}
    except WorkflowError as error:
        fail(error)


@app.get("/")
def frontend() -> FileResponse:
    return FileResponse(CONSOLE / "index.html")


app.mount("/", StaticFiles(directory=CONSOLE), name="frontend")

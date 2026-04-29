"""A2A wrapper around the Foundry test agent.

Foundry Agent Service does NOT natively expose agents over A2A. To publish
via A2A you stand up an A2A-compatible HTTP endpoint that proxies to the
Foundry agent, then register that URL in Foundry Control Plane.

This module exposes:
  GET  /.well-known/agent-card.json   — A2A discovery document
  POST /a2a/messages                   — A2A message endpoint (proxies to Foundry)

Run locally:
  PROJECT_ENDPOINT=... HAIKU_DEPLOYMENT_NAME=... \\
    uvicorn a2a_server:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import os
from typing import Any

from azure.ai.agents import AgentsClient
from azure.identity import DefaultAzureCredential
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agent import AGENT_NAME

app = FastAPI(title="foundry-lab-a2a")

# Telemetry. configure_azure_monitor() reads APPLICATIONINSIGHTS_CONNECTION_STRING
# from the env (injected by Terraform from the project's App Insights connection).
# Each step prints a [telemetry] line at startup so Container Apps console logs
# show exactly which path ran — silent no-ops here previously masked which of
# {env-missing, import-failure, exporter-init} was the actual fault.
_cs = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
print(f"[telemetry] APPLICATIONINSIGHTS_CONNECTION_STRING len={len(_cs)}", flush=True)

if _cs:
    from azure.monitor.opentelemetry import configure_azure_monitor
    from opentelemetry import trace
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    configure_azure_monitor()
    FastAPIInstrumentor.instrument_app(app)
    print("[telemetry] configure_azure_monitor + FastAPIInstrumentor ok", flush=True)

    try:
        from azure.ai.agents.telemetry import AIAgentsInstrumentor

        AIAgentsInstrumentor().instrument()
        print("[telemetry] AIAgentsInstrumentor ok", flush=True)
    except ImportError as e:
        print(f"[telemetry] AIAgentsInstrumentor unavailable: {e}", flush=True)

    # Container Apps with min_replicas=0 SIGTERMs the container after idle. The
    # BatchSpanProcessor's default 5s flush interval can lose the last batch if
    # uvicorn doesn't drive OTel's atexit hook before the kill — force_flush in
    # the lifespan shutdown closes that gap.
    @app.on_event("shutdown")
    def _flush_telemetry() -> None:
        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(10_000)
            print("[telemetry] force_flush complete", flush=True)

_agents: AgentsClient | None = None
_agent_id_cache: str | None = None


def _agents_client() -> AgentsClient:
    global _agents
    if _agents is None:
        _agents = AgentsClient(
            endpoint=os.environ["PROJECT_ENDPOINT"],
            credential=DefaultAzureCredential(),
        )
    return _agents


def _agent_id() -> str:
    # Foundry's Assistants runtime requires the asst_* id, not the human name.
    # agent.py creates the agent at deploy time; this lookup binds the wrapper
    # to whichever id that produced.
    global _agent_id_cache
    if _agent_id_cache is None:
        client = _agents_client()
        match = next(
            (a for a in client.list_agents() if getattr(a, "name", None) == AGENT_NAME),
            None,
        )
        if match is None:
            raise HTTPException(503, f"agent {AGENT_NAME!r} not found — run agent.py against this project")
        _agent_id_cache = match.id
    return _agent_id_cache


@app.get("/.well-known/agent-card.json")
def agent_card() -> dict[str, Any]:
    # A2A agent-card spec: https://a2a.googleapis.com / Microsoft A2A registration docs
    public_url = os.environ.get("A2A_PUBLIC_URL", "http://localhost:8080")
    return {
        "name": AGENT_NAME,
        "description": "Foundry Lab test agent wrapped for A2A.",
        "url": f"{public_url}/a2a/messages",
        "version": "0.1.0",
        "protocols": ["a2a/v1"],
        "capabilities": {"streaming": False, "tools": []},
        "provider": {"organization": "foundry-lab"},
        "skills": [
            {
                "id": "chat",
                "name": "chat",
                "description": "General chat via Claude Haiku 4.5 on Foundry.",
            }
        ],
    }


class A2AMessage(BaseModel):
    role: str
    parts: list[dict[str, Any]]


class A2ARequest(BaseModel):
    messages: list[A2AMessage]
    threadId: str | None = None


@app.post("/a2a/messages")
def a2a_messages(req: A2ARequest) -> dict[str, Any]:
    client = _agents_client()
    user_text = next(
        (p.get("text", "") for m in req.messages if m.role == "user" for p in m.parts if p.get("type") == "text"),
        "",
    )
    if not user_text:
        raise HTTPException(400, "no user text part")

    thread = client.threads.create() if not req.threadId else client.threads.get(req.threadId)
    client.messages.create(thread_id=thread.id, role="user", content=user_text)
    run = client.runs.create_and_process(thread_id=thread.id, agent_id=_agent_id())

    if run.status != "completed":
        raise HTTPException(502, f"agent run failed: {run.status}")

    msgs = list(client.messages.list(thread_id=thread.id, order="desc", limit=1))
    reply = msgs[0].content[0].text.value if msgs else ""

    return {
        "threadId": thread.id,
        "messages": [{"role": "assistant", "parts": [{"type": "text", "text": reply}]}],
    }

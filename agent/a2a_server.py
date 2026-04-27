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
# AIAgentsInstrumentor adds spans for Foundry agent runs on top of the HTTP-level
# spans FastAPIInstrumentor provides. Skipped when the connection string isn't
# set so local `uvicorn` runs don't fail.
if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    from azure.monitor.opentelemetry import configure_azure_monitor
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    configure_azure_monitor()
    FastAPIInstrumentor.instrument_app(app)

    try:
        from azure.ai.agents.telemetry import AIAgentsInstrumentor

        AIAgentsInstrumentor().instrument()
    except ImportError:
        # Older azure-ai-agents builds don't ship the telemetry submodule;
        # HTTP-level spans from FastAPIInstrumentor still go through.
        pass

_agents: AgentsClient | None = None


def _agents_client() -> AgentsClient:
    global _agents
    if _agents is None:
        _agents = AgentsClient(
            endpoint=os.environ["PROJECT_ENDPOINT"],
            credential=DefaultAzureCredential(),
        )
    return _agents


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
    run = client.runs.create_and_process(thread_id=thread.id, agent_id=AGENT_NAME)

    if run.status != "completed":
        raise HTTPException(502, f"agent run failed: {run.status}")

    msgs = list(client.messages.list(thread_id=thread.id, order="desc", limit=1))
    reply = msgs[0].content[0].text.value if msgs else ""

    return {
        "threadId": thread.id,
        "messages": [{"role": "assistant", "parts": [{"type": "text", "text": reply}]}],
    }

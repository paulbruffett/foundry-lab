"""A2A wrapper that proxies to Claude on Azure Foundry.

Foundry's Agents/Assistants service (threads + runs + messages) only supports
Azure-OpenAI backing models — Anthropic deployments are not invokable through
that runtime and return `invalid_deployment` / `api_not_supported`. So this
wrapper calls Foundry's native Anthropic Messages pass-through directly at
`https://<account>.services.ai.azure.com/anthropic/v1/messages` (account-
scoped, no api-version — Foundry versions this route via the path's /v1/).
Token audience must be `https://ai.azure.com`; the more common
`https://cognitiveservices.azure.com` audience causes Foundry to silently
drop the request (manifests as a 60s read timeout, not a 401).

The wrapper is stateless: A2A clients are expected to send full conversation
history on each call. The optional `threadId` field is accepted for spec
compatibility but ignored.

Required env vars:
  PROJECT_ENDPOINT        — e.g. https://<resource>.services.ai.azure.com/api/projects/<proj>
  HAIKU_DEPLOYMENT_NAME   — Anthropic deployment name in the Foundry account
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

import requests
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

AGENT_NAME = "foundry-lab-test-agent"
INSTRUCTIONS = "You are the Foundry Lab test agent.\nAnswer concisely."

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("foundry-lab-a2a")
logger.setLevel(logging.INFO)

app = FastAPI(title="foundry-lab-a2a")

# Telemetry. configure_azure_monitor() reads APPLICATIONINSIGHTS_CONNECTION_STRING
# from the env (injected by Terraform from the project's App Insights connection)
# and wires Python's logging module into the OTel logs exporter, so logger.info /
# logger.exception land in App Insights `traces` and `exceptions`. Plain print()
# does NOT — keep diagnostic output going through logger.
_cs = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
logger.info("telemetry connection string len=%d", len(_cs))

if _cs:
    from azure.monitor.opentelemetry import configure_azure_monitor
    from opentelemetry import trace
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.requests import RequestsInstrumentor

    configure_azure_monitor(logger_name="foundry-lab-a2a")
    FastAPIInstrumentor.instrument_app(app)
    RequestsInstrumentor().instrument()
    logger.info("telemetry configured: azure_monitor + fastapi + requests")

    # Container Apps with min_replicas=0 SIGTERMs the container after idle. The
    # BatchSpanProcessor's default 5s flush interval can lose the last batch if
    # uvicorn doesn't drive OTel's atexit hook before the kill — force_flush in
    # the lifespan shutdown closes that gap.
    @app.on_event("shutdown")
    def _flush_telemetry() -> None:
        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(10_000)
            logger.info("telemetry force_flush complete")


_token_provider = None


def _token() -> str:
    # Foundry's Anthropic pass-through validates audience == https://ai.azure.com.
    # The cognitiveservices.azure.com scope (which works for Azure-OpenAI on
    # Foundry) is rejected here with "audience is incorrect (https://ai.azure.com)".
    global _token_provider
    if _token_provider is None:
        _token_provider = get_bearer_token_provider(
            DefaultAzureCredential(),
            "https://ai.azure.com/.default",
        )
    return _token_provider()


def _anthropic_url() -> str:
    # Account-scoped pass-through; the project-scoped variant rejects every
    # api-version we tried (project + no version = "Missing api-version",
    # project + any value = "API version not supported" or 404). The account
    # host with no api-version returns 200 — Foundry versions this route via
    # the /v1/ in the path, not via an Azure-style api-version query.
    # The earlier 60s hang on this same URL was caused by the token audience
    # being https://cognitiveservices.azure.com instead of https://ai.azure.com;
    # Foundry silently drops the request when the audience is wrong rather
    # than returning 401.
    parsed = urlparse(os.environ["PROJECT_ENDPOINT"])
    return f"{parsed.scheme}://{parsed.netloc}/anthropic/v1/messages"


@app.get("/.well-known/agent-card.json")
def agent_card() -> dict[str, Any]:
    public_url = os.environ.get("A2A_PUBLIC_URL", "http://localhost:8080")
    return {
        "name": AGENT_NAME,
        "description": "Foundry Lab test agent (Claude Haiku 4.5 on Foundry).",
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


def _to_anthropic_messages(messages: list[A2AMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        text = "".join(p.get("text", "") for p in m.parts if p.get("type") == "text")
        if not text:
            continue
        role = "user" if m.role == "user" else "assistant"
        out.append({"role": role, "content": text})
    return out


@app.post("/a2a/messages")
def a2a_messages(req: A2ARequest) -> dict[str, Any]:
    try:
        anth_messages = _to_anthropic_messages(req.messages)
        if not anth_messages:
            raise HTTPException(400, "no text content in messages")
        if anth_messages[0]["role"] != "user":
            raise HTTPException(400, "first message must be from the user")

        # Surface missing config as 500 with a clear log entry, not a bare KeyError.
        deployment = os.environ.get("HAIKU_DEPLOYMENT_NAME")
        if not deployment:
            logger.error("HAIKU_DEPLOYMENT_NAME env var is not set")
            raise HTTPException(500, "HAIKU_DEPLOYMENT_NAME not configured")
        if not os.environ.get("PROJECT_ENDPOINT"):
            logger.error("PROJECT_ENDPOINT env var is not set")
            raise HTTPException(500, "PROJECT_ENDPOINT not configured")

        url = _anthropic_url()
        logger.info("a2a request: messages=%d url=%s deployment=%s", len(anth_messages), url, deployment)

        payload = {
            "model": deployment,
            "max_tokens": 4096,
            "system": INSTRUCTIONS,
            "messages": anth_messages,
        }

        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {_token()}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            json=payload,
            timeout=15,
        )
        if not r.ok:
            logger.error("anthropic upstream error status=%d body=%s", r.status_code, r.text[:500])
            raise HTTPException(502, f"upstream error: HTTP {r.status_code}: {r.text[:500]}")

        data = r.json()
        reply = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        logger.info("a2a reply len=%d", len(reply))

        return {
            "threadId": req.threadId,
            "messages": [{"role": "assistant", "parts": [{"type": "text", "text": reply}]}],
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("unhandled exception in /a2a/messages")
        raise HTTPException(500, "internal error — see App Insights exceptions")

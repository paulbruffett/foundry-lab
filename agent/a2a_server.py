"""A2A wrapper that proxies to Claude on Azure Foundry.

Foundry's Agents/Assistants service (threads + runs + messages) only supports
Azure-OpenAI backing models — Anthropic deployments are not invokable through
that runtime and return `invalid_deployment` / `api_not_supported`. So this
wrapper calls Foundry's native Anthropic Messages pass-through via the
`AnthropicFoundry` SDK client, pointed at
`https://<account>.services.ai.azure.com/anthropic` (account-scoped; the SDK
appends `/v1/messages`). Token audience must be `https://ai.azure.com`; the
more common `https://cognitiveservices.azure.com` audience causes Foundry to
silently drop the request (manifests as a 60s read timeout, not a 401).

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
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

import anthropic
from anthropic import AnthropicFoundry
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from pydantic import BaseModel

AGENT_NAME = "foundry-lab-test-agent"
INSTRUCTIONS = "You are the Foundry Lab test agent.\nAnswer concisely."

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("foundry-lab-a2a")
logger.setLevel(logging.INFO)

# get_tracer returns a proxy; spans created later route to whichever provider
# configure_azure_monitor installs (or a no-op provider if telemetry is off).
_tracer = trace.get_tracer("foundry-lab-a2a")

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
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    configure_azure_monitor(logger_name="foundry-lab-a2a")
    FastAPIInstrumentor.instrument_app(app)
    # The Anthropic SDK uses httpx internally; instrumenting it keeps the
    # upstream Foundry call visible as a dependency span in App Insights.
    HTTPXClientInstrumentor().instrument()
    logger.info("telemetry configured: azure_monitor + fastapi + httpx")

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


@lru_cache(maxsize=1)
def _client() -> AnthropicFoundry:
    # Account-scoped pass-through; the project-scoped variant rejects every
    # api-version we tried. The SDK targets `<base_url>/v1/messages`, so
    # base_url ends at `/anthropic`.
    # The earlier 60s hang on this URL was caused by the token audience being
    # https://cognitiveservices.azure.com instead of https://ai.azure.com;
    # Foundry silently drops the request when the audience is wrong rather
    # than returning 401.
    parsed = urlparse(os.environ["PROJECT_ENDPOINT"])
    base_url = f"{parsed.scheme}://{parsed.netloc}/anthropic"
    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(),
        "https://ai.azure.com/.default",
    )
    return AnthropicFoundry(
        azure_ad_token_provider=token_provider,
        base_url=base_url,
        timeout=15.0,
    )


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

        logger.info("a2a request: messages=%d deployment=%s", len(anth_messages), deployment)

        # OTel GenAI semantic-convention span — Foundry's Tracing tab keys off
        # `gen_ai.*` attributes to render this as an LLM call rather than a
        # generic HTTP dependency. Span name is `{op} {model}` per the spec.
        with _tracer.start_as_current_span(
            f"chat {deployment}",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.system": "anthropic",
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": deployment,
                "gen_ai.request.max_tokens": 4096,
                "gen_ai.request.messages.count": len(anth_messages),
            },
        ) as span:
            try:
                message = _client().messages.create(
                    model=deployment,
                    max_tokens=4096,
                    system=INSTRUCTIONS,
                    messages=anth_messages,
                )
            except anthropic.APIStatusError as e:
                span.set_status(Status(StatusCode.ERROR, f"HTTP {e.status_code}"))
                span.record_exception(e)
                body = getattr(e, "response", None)
                body_text = body.text[:500] if body is not None else ""
                logger.error("anthropic upstream error status=%d body=%s", e.status_code, body_text)
                raise HTTPException(502, f"upstream error: HTTP {e.status_code}: {body_text}") from e

            span.set_attributes(
                {
                    "gen_ai.response.id": message.id,
                    "gen_ai.response.model": message.model,
                    "gen_ai.response.finish_reasons": [message.stop_reason] if message.stop_reason else [],
                    "gen_ai.usage.input_tokens": message.usage.input_tokens,
                    "gen_ai.usage.output_tokens": message.usage.output_tokens,
                }
            )

        reply = "".join(b.text for b in message.content if b.type == "text")
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

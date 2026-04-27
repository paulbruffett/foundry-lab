"""Idempotently upsert the test agent inside the Foundry project.

Agents in Foundry are data-plane objects (no ARM/azapi resource), so this
script runs after `terraform apply` completes. It's safe to re-run on every
check-in — `create_version` makes a new version of the same agent name.

Required env vars:
  PROJECT_ENDPOINT        — from `terraform output project_endpoint`
  HAIKU_DEPLOYMENT_NAME   — from `terraform output haiku_deployment_name`
"""

from __future__ import annotations

import json
import os
import sys

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential

AGENT_NAME = "foundry-lab-test-agent"

INSTRUCTIONS = """You are the Foundry Lab test agent.
Answer concisely."""


def _existing_version(client: AIProjectClient) -> object | None:
    try:
        return client.agents.get_version(agent_name=AGENT_NAME, version="latest")
    except Exception as e:
        print(f"could not fetch existing version, will attempt create: {e}", file=sys.stderr)
        return None


def _definition_unchanged(existing: object | None, deployment: str) -> bool:
    if existing is None:
        return False
    defn = getattr(existing, "definition", existing)
    return (
        getattr(defn, "model", None) == deployment
        and getattr(defn, "instructions", None) == INSTRUCTIONS
        and not (getattr(defn, "tools", None) or [])
    )


def main() -> int:
    endpoint = os.environ["PROJECT_ENDPOINT"]
    deployment = os.environ["HAIKU_DEPLOYMENT_NAME"]

    client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())

    existing = _existing_version(client)
    if _definition_unchanged(existing, deployment):
        print(json.dumps({
            "agent_name": AGENT_NAME,
            "version": getattr(existing, "version", None),
            "id": getattr(existing, "id", None),
            "skipped": "definition unchanged",
        }, indent=2))
        return 0

    definition = PromptAgentDefinition(
        model=deployment,
        instructions=INSTRUCTIONS,
        tools=[],
    )

    try:
        version = client.agents.create_version(
            agent_name=AGENT_NAME,
            definition=definition,
        )
    except HttpResponseError as e:
        print(f"create_version failed: status={e.status_code} reason={e.reason}", file=sys.stderr)
        if e.response is not None:
            try:
                print(f"response body: {e.response.text()}", file=sys.stderr)
            except Exception as body_err:
                print(f"could not read response body: {body_err}", file=sys.stderr)
            print(f"x-ms-request-id: {e.response.headers.get('x-ms-request-id')}", file=sys.stderr)
        raise

    print(json.dumps({
        "agent_name": AGENT_NAME,
        "version": getattr(version, "version", None),
        "id": getattr(version, "id", None),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

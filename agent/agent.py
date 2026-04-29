"""Idempotently upsert the test agent inside the Foundry project.

Agents in Foundry are data-plane objects (no ARM/azapi resource), so this
script runs after `terraform apply` completes. Re-runs are no-ops when the
definition matches; otherwise the existing assistant is updated in place.

Required env vars:
  PROJECT_ENDPOINT        — from `terraform output project_endpoint`
  HAIKU_DEPLOYMENT_NAME   — from `terraform output haiku_deployment_name`
"""

from __future__ import annotations

import json
import os
import sys

from azure.ai.agents import AgentsClient
from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential

AGENT_NAME = "foundry-lab-test-agent"

INSTRUCTIONS = """You are the Foundry Lab test agent.
Answer concisely."""


def _find_existing(client: AgentsClient):
    return next(
        (a for a in client.list_agents() if getattr(a, "name", None) == AGENT_NAME),
        None,
    )


def _unchanged(existing, deployment: str) -> bool:
    if existing is None:
        return False
    return (
        getattr(existing, "model", None) == deployment
        and getattr(existing, "instructions", None) == INSTRUCTIONS
        and not (getattr(existing, "tools", None) or [])
    )


def main() -> int:
    endpoint = os.environ["PROJECT_ENDPOINT"]
    deployment = os.environ["HAIKU_DEPLOYMENT_NAME"]

    client = AgentsClient(endpoint=endpoint, credential=DefaultAzureCredential())

    try:
        existing = _find_existing(client)
        if existing is None:
            agent = client.create_agent(
                model=deployment,
                name=AGENT_NAME,
                instructions=INSTRUCTIONS,
            )
            action = "created"
        elif _unchanged(existing, deployment):
            agent = existing
            action = "unchanged"
        else:
            agent = client.update_agent(
                existing.id,
                model=deployment,
                instructions=INSTRUCTIONS,
                tools=[],
            )
            action = "updated"
    except HttpResponseError as e:
        print(f"agent upsert failed: status={e.status_code} reason={e.reason}", file=sys.stderr)
        if e.response is not None:
            try:
                print(f"response body: {e.response.text()}", file=sys.stderr)
            except Exception as body_err:
                print(f"could not read response body: {body_err}", file=sys.stderr)
            print(f"x-ms-request-id: {e.response.headers.get('x-ms-request-id')}", file=sys.stderr)
        raise

    print(json.dumps({
        "agent_name": AGENT_NAME,
        "id": agent.id,
        "action": action,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.identity import DefaultAzureCredential

AGENT_NAME = "foundry-lab-test-agent"
MODEL_CARD = Path(__file__).parent / "model_card.md"

INSTRUCTIONS = """You are the Foundry Lab test agent.
Answer concisely. If asked about your model or capabilities, point the user
to the model card published alongside this agent."""


def main() -> int:
    endpoint = os.environ["PROJECT_ENDPOINT"]
    deployment = os.environ["HAIKU_DEPLOYMENT_NAME"]

    client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())

    # PromptAgentDefinition doesn't currently accept arbitrary metadata.
    # Model-card pointer lives on the deployment as a tag (see main.tf);
    # the A2A wrapper publishes the agent's discovery card separately.
    definition = PromptAgentDefinition(
        model=deployment,
        instructions=INSTRUCTIONS,
        tools=[],
    )

    version = client.agents.create_version(
        agent_name=AGENT_NAME,
        definition=definition,
    )

    print(json.dumps({
        "agent_name": AGENT_NAME,
        "version": getattr(version, "version", None),
        "id": getattr(version, "id", None),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

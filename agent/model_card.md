# Model Card — Foundry Lab Test Agent

## Overview
- **Agent name:** foundry-lab-test-agent
- **Base model:** Anthropic Claude Haiku 4.5 (`claude-haiku-4-5`, version `20251001`)
- **Hosted on:** Microsoft Foundry (Models-from-Partners), GlobalStandard deployment
- **Protocols:** Anthropic Messages API (native), A2A v1 (via wrapper)
- **Owner:** foundry-lab

## Intended use
General-purpose chat assistant for internal experimentation with the Foundry
Agent Service. Not for production workloads, PHI/PII, or regulated decisions.

## Inputs and outputs
- **Input:** text messages (user role)
- **Output:** text completions
- **Context window:** per Claude Haiku 4.5 catalog card
- **Tools:** none in MVP

## Safety
- Anthropic models on Foundry don't accept an Azure RAI policy; Anthropic's
  own safety classifiers run server-side. If you need additional Azure
  content-safety filtering, route requests through Azure AI Content Safety
  separately before/after the model call.
- A2A wrapper does no auth today — gate the public URL behind APIM or an
  ingress with Entra auth before registering it in Foundry Control Plane.

## Evaluation
None in MVP. Add evals via `azure-ai-projects` `project.evaluations` once
the agent is in use.

## Change log
- v0.1.0 — initial MVP, Terraform-managed deployment, Python agent upsert.

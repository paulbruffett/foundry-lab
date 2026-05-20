# Foundry Lab

A reference setup that deploys **Claude Haiku 4.5** into an Azure AI Foundry project and publishes it through an **A2A (Agent-to-Agent) wrapper** so other systems can call it as a first-class agent endpoint.

Foundry's Agents/Assistants service only supports Azure-OpenAI backing models — Anthropic deployments aren't invokable through that runtime. The wrapper in [agent/](agent/) sidesteps that by calling Foundry's native Anthropic Messages API directly at `/anthropic/v1/messages`, behind an EasyAuth-gated Container App, and serves an A2A agent card so it can be registered with Foundry Control Plane and consumed by other A2A-aware clients.

## Repo layout

```
terraform/   IaC: Foundry project, Claude Haiku deployment, ACR + Container Apps + EasyAuth, App Insights wiring.
agent/       The A2A wrapper (FastAPI). Calls Foundry's Anthropic Messages API with a managed-identity bearer token.
.github/     CI: terraform plan/apply on PRs/main + acr build + container app image roll.
```

## Setup

All one-time infra setup (Terraform state backend, service principal + role assignments, Marketplace agreement, Entra app reg for the A2A gate, post-apply scope/preauthorization, Foundry Control Plane registration) lives in [terraform/README.md](terraform/README.md).

Once that's run, the rest of this document covers how to actually call the deployed agent.

## Using the deployed agent

The endpoint is a Container App fronted by EasyAuth. Callers must present a bearer token whose audience is `api://<A2A_AAD_CLIENT_ID>` — the wrapper itself does no validation; EasyAuth is the gate.

### 1. Acquire a token

```bash
A2A_APP_ID=$(az ad app list --display-name "foundry-lab-a2a" --query "[0].appId" -o tsv)
A2A_FQDN=$(az containerapp show -n foundry-lab-a2a -g foundry-lab-a2a \
  --query "properties.configuration.ingress.fqdn" -o tsv)
TOKEN=$(az account get-access-token --resource "api://$A2A_APP_ID" --query accessToken -o tsv)
```

Tokens last ~1 hour. If `get-access-token` fails with `AADSTS65001`, the `user_impersonation` scope + Azure CLI preauthorization haven't been added to the app reg yet — see post-terraform setup in [terraform/README.md](terraform/README.md#1-expose-an-api-scope-and-pre-authorize-azure-cli).

Note: this won't work from Azure Cloud Shell — Cloud Shell auths via Managed Identity, and MSI can only mint tokens for a fixed set of audiences (ARM, Graph, Key Vault, etc.), not custom app registrations. Run from a local terminal where you can `az login` as a user, or use `az login --use-device-code --scope "api://$A2A_APP_ID/.default"` inside Cloud Shell to override the MSI default.

### 2. Fetch the agent card

```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://$A2A_FQDN/.well-known/agent-card.json" | jq .
```

A healthy response returns the A2A agent descriptor — name, version, capabilities, and the URL of the messages endpoint to call next. Same bearer token is reused for that call.

### 3. Send a message

The wrapper is stateless: clients send full conversation history on each call. The `threadId` field is accepted for spec compatibility but ignored.

```bash
curl -sS -X POST "https://$A2A_FQDN/a2a/messages" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "parts": [{"type": "text", "text": "Hello, who are you?"}]}
    ]
  }' | jq .
```

Response shape:

```json
{
  "threadId": null,
  "messages": [
    {"role": "assistant", "parts": [{"type": "text", "text": "..."}]}
  ]
}
```

### Postman

Authorization tab → **Bearer Token** → paste the JWT → `GET https://<A2A_FQDN>/.well-known/agent-card.json`, then `POST https://<A2A_FQDN>/a2a/messages` with the body above. The agent card advertises the protocol URLs so any A2A-aware client can discover them automatically.

### From other agents / Foundry Control Plane

After the post-terraform Control Plane registration step, the endpoint is invokable as a connected agent from any Foundry project — same bearer-token gate, but Foundry handles the token exchange.

## Observability

Each request emits OpenTelemetry traces through the Azure Monitor exporter (App Insights connection string is injected into the container by Terraform). Startup logs include `[telemetry]` lines so Container Apps console output shows which path ran — useful when a silent no-op would otherwise mask whether telemetry actually initialized.

## Gotchas

- **Region:** Claude Haiku 4.5 only ships in `eastus2` and `swedencentral`.
- **No Azure content filter on Claude:** Anthropic's classifiers run server-side; Anthropic deployments don't accept `raiPolicyName`. Add Azure AI Content Safety in front of the wrapper if you need extra filtering.
- **Min-replicas = 0:** the Container App scales to zero. First request after idle pays a cold-start; subsequent requests are warm. The wrapper force-flushes telemetry on SIGTERM so the last batch isn't lost on scale-down.
- **Stateless wrapper:** no thread/session storage. Clients are responsible for sending full history on each call.

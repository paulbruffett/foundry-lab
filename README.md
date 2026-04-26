# Terraform — Foundry Lab

Manages an Azure AI Foundry **project** + **Claude Haiku 4.5 deployment** under a pre-existing Foundry account (Microsoft.CognitiveServices, kind = `AIServices`), plus the **A2A wrapper** (Container Apps + ACR) that publishes the agent for cross-platform consumption. Agents themselves are created out-of-band by `../agent/agent.py` because they're a Foundry data-plane construct, not an ARM resource.

```
providers.tf            azapi + azurerm provider pinning
backend.tf              azurerm remote state (Entra auth)
variables.tf            inputs + region validation
main.tf                 Foundry project + Claude Haiku deployment
a2a.tf                  ACR + Container Apps + EasyAuth for the A2A wrapper
outputs.tf              endpoints + IDs consumed by the agent + a2a steps
terraform.tfvars.example
```

## One-time bootstrap

You need: an Azure subscription (Enterprise or MCA-E for Claude), an existing Foundry account, the Anthropic Marketplace agreement signed, and an app registration for GitHub Actions to authenticate as.

### 1. Variables you'll reuse

```bash
SUB_ID="<subscription id>"
TF_STATE_RG="aiservice"                          # RG that holds the state account
TF_STATE_SA="pbaiserx"                           # globally-unique storage account name
TF_STATE_CONTAINER="tfstate"
LOCATION="eastus2"                               # state account region (TF state, not the Foundry resources)

FOUNDRY_RG="<rg of the existing Foundry account>"
FOUNDRY_ACCOUNT="<name of the existing Foundry account>"

az account set --subscription "$SUB_ID"
```

### 2. Verify the existing Foundry account is project-capable

```bash
az cognitiveservices account show -n "$FOUNDRY_ACCOUNT" -g "$FOUNDRY_RG" \
  --query properties.allowProjectManagement
```

Must return `true`. If `false`, toggle it via the portal (Foundry account → Properties) or:

```bash
az resource update \
  --ids "/subscriptions/$SUB_ID/resourceGroups/$FOUNDRY_RG/providers/Microsoft.CognitiveServices/accounts/$FOUNDRY_ACCOUNT" \
  --set properties.allowProjectManagement=true
```

### 3. Create the Terraform state backend

```bash
az group create -n "$TF_STATE_RG" -l "$LOCATION"

az storage account create \
  -n "$TF_STATE_SA" -g "$TF_STATE_RG" -l "$LOCATION" \
  --sku Standard_LRS \
  --kind StorageV2 \
  --min-tls-version TLS1_2 \
  --allow-blob-public-access false

az storage container create \
  -n "$TF_STATE_CONTAINER" \
  --account-name "$TF_STATE_SA" \
  --auth-mode login
```

### 4. App registration for GitHub Actions

Skip if you already have one. Two auth options — **client secret** (simpler, matches the current workflow) or **OIDC federated credentials** (recommended for production, no long-lived credentials to rotate).

```bash
APP_NAME="gh-foundry-lab"
APP_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)
SP_OBJECT_ID=$(az ad sp create --id "$APP_ID" --query id -o tsv)

echo "AZURE_CLIENT_ID  = $APP_ID"
echo "AZURE_TENANT_ID  = $(az account show --query tenantId -o tsv)"
echo "SP object id     = $SP_OBJECT_ID"
```

**Option A — client secret** (matches the current workflow):

```bash
az ad app credential reset --id "$APP_ID" --display-name "gh-actions" --years 1 \
  --query password -o tsv
# store this output as the AZURE_CLIENT_SECRET GitHub secret — Azure displays it only once
```

**Option B — OIDC federated credentials**:

```bash
GITHUB_ORG="<your-github-username-or-org>"
GITHUB_REPO="foundry-lab"

az ad app federated-credential create --id "$APP_ID" --parameters '{
  "name":"gh-main",
  "issuer":"https://token.actions.githubusercontent.com",
  "subject":"repo:'"$GITHUB_ORG"'/'"$GITHUB_REPO"':ref:refs/heads/main",
  "audiences":["api://AzureADTokenExchange"]
}'
az ad app federated-credential create --id "$APP_ID" --parameters '{
  "name":"gh-pr",
  "issuer":"https://token.actions.githubusercontent.com",
  "subject":"repo:'"$GITHUB_ORG"'/'"$GITHUB_REPO"':pull_request",
  "audiences":["api://AzureADTokenExchange"]
}'
```

If you pick OIDC, swap the `azure/login` step in `.github/workflows/terraform.yml` to:

```yaml
permissions:
  id-token: write
  contents: read
  pull-requests: write

# inside the job
- uses: azure/login@v2
  with:
    client-id: ${{ secrets.AZURE_CLIENT_ID }}
    tenant-id: ${{ secrets.AZURE_TENANT_ID }}
    subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
```

…add `ARM_USE_OIDC: "true"` to the env block, and drop the `AZURE_CLIENT_SECRET` secret.

### 5. Role assignments

Without these the SP authenticates but `az account list` returns empty (`No subscriptions found`) and Terraform 403s on every API call.

```bash
# state container — required by the azurerm backend (use_azuread_auth = true)
az role assignment create \
  --assignee-object-id "$SP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" \
  --scope "$(az storage account show -n "$TF_STATE_SA" -g "$TF_STATE_RG" --query id -o tsv)"

# Foundry account RG — create projects + deployments
az role assignment create \
  --assignee-object-id "$SP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Cognitive Services Contributor" \
  --scope "/subscriptions/$SUB_ID/resourceGroups/$FOUNDRY_RG"

# Foundry data plane — required for agent.py to upsert the agent
az role assignment create \
  --assignee-object-id "$SP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Azure AI Project Manager" \
  --scope "/subscriptions/$SUB_ID/resourceGroups/$FOUNDRY_RG"

# A2A RG — Contributor needed for Container Apps + ACR + Log Analytics
A2A_RG="foundry-lab-a2a"
az group create -n "$A2A_RG" -l "$LOCATION"

az role assignment create \
  --assignee-object-id "$SP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Contributor" \
  --scope "/subscriptions/$SUB_ID/resourceGroups/$A2A_RG"
```

### 5a. Entra app registration for the A2A EasyAuth gate

The A2A Container App is fronted by Container Apps EasyAuth. Create a single-tenant app registration whose `clientId` is what EasyAuth treats as the audience.

```bash
A2A_APP_NAME="foundry-lab-a2a"
A2A_APP_ID=$(az ad app create --display-name "$A2A_APP_NAME" --query appId -o tsv)
az ad app update --id "$A2A_APP_ID" --identifier-uris "api://$A2A_APP_ID"
echo "A2A_AAD_CLIENT_ID = $A2A_APP_ID"
```

Callers acquire a token for `api://$A2A_APP_ID` (e.g. `az account get-access-token --resource api://$A2A_APP_ID`) and pass it as `Authorization: Bearer <token>`. EasyAuth rejects unauthenticated requests with `401`.

### 6. Sign the Anthropic Marketplace agreement

Claude is delivered as a Models-from-Partners offer. The first deployment will fail until the agreement is accepted.

```bash
az provider register --namespace Microsoft.SaaS
# Then in the Azure portal: Marketplace → search "Anthropic" → Subscribe.
# This is a one-time, per-subscription action and cannot be done with `az` alone.
```

### 7. GitHub repo configuration

**Secrets** (repo Settings → Secrets and variables → Actions → Secrets):

| Name | Value |
| --- | --- |
| `AZURE_CLIENT_ID` | `$APP_ID` from step 4 |
| `AZURE_CLIENT_SECRET` | password from step 4 (option A only) |
| `AZURE_TENANT_ID` | `az account show --query tenantId -o tsv` |
| `AZURE_SUBSCRIPTION_ID` | `$SUB_ID` |

**Variables** (same page → Variables tab):

| Name | Value |
| --- | --- |
| `TF_STATE_RESOURCE_GROUP` | `$TF_STATE_RG` |
| `TF_STATE_STORAGE_ACCOUNT` | `$TF_STATE_SA` |
| `TF_STATE_CONTAINER` | `$TF_STATE_CONTAINER` |
| `FOUNDRY_ACCOUNT_NAME` | `$FOUNDRY_ACCOUNT` |
| `FOUNDRY_ACCOUNT_RESOURCE_GROUP` | `$FOUNDRY_RG` |
| `PROJECT_NAME` | e.g. `foundry-lab` |
| `LOCATION` | `eastus2` or `swedencentral` (Claude-supported regions only) |
| `A2A_RESOURCE_GROUP` | `$A2A_RG` from step 5 |
| `ACR_NAME` | globally-unique alphanumeric ACR name (5-50 chars) |
| `A2A_AAD_CLIENT_ID` | `$A2A_APP_ID` from step 5a |

## Local development

```bash
cp terraform.tfvars.example terraform.tfvars
# fill in subscription_id, foundry_account_name, foundry_account_resource_group

az login
terraform init \
  -backend-config="resource_group_name=$TF_STATE_RG" \
  -backend-config="storage_account_name=$TF_STATE_SA" \
  -backend-config="container_name=$TF_STATE_CONTAINER" \
  -backend-config="key=foundry-lab.tfstate"

terraform plan
terraform apply
```

After `apply` succeeds, run the data-plane agent upsert:

```bash
export PROJECT_ENDPOINT=$(terraform output -raw project_endpoint)
export HAIKU_DEPLOYMENT_NAME=$(terraform output -raw haiku_deployment_name)
cd ../agent && pip install -r requirements.txt && python agent.py
```

## CI behavior

`.github/workflows/terraform.yml` runs on every push and PR touching `terraform/`, `agent/`, or the workflow itself:

- PRs → `terraform plan` only.
- Push to `main` → three sequential jobs:
  1. **terraform** — `plan` + `apply` (Foundry project, Haiku deployment, ACR, Container Apps env, Container App, EasyAuth).
  2. **agent** — `agent.py` upserts the Foundry test agent against the freshly-applied project.
  3. **a2a** — `az acr build` builds the wrapper image from `../agent/Dockerfile`, pushes to ACR, then `az containerapp update --image` rolls the Container App. The job logs the public URL and the discovery-document path you paste into Foundry Control Plane.

The Container App is created with a public placeholder image (`mcr.microsoft.com/azuredocs/containerapps-helloworld`) and `lifecycle.ignore_changes` on the image field — first apply will succeed even though the wrapper isn't built yet, and CI takes over from there.

## Registering the A2A endpoint with Foundry Control Plane

There is no clean Terraform/azapi resource for A2A registration today. After the `a2a` job logs the public URL, paste `${A2A_PUBLIC_URL}/.well-known/agent-card.json` into **Foundry Control Plane → Connected agents → Add A2A endpoint** in the portal.

## Gotchas

- **Region:** Claude Haiku 4.5 only ships in `eastus2` and `swedencentral`. `variables.tf` enforces this.
- **No Azure content filter on Claude:** Anthropic's classifiers run server-side — Anthropic deployments don't accept `raiPolicyName`. Route through Azure AI Content Safety separately if you need extra filtering before/after the model call.
- **A2A auth:** the wrapper itself does no token validation; EasyAuth on the Container App ingress is the gate. Callers must present a bearer token whose audience is `api://$A2A_AAD_CLIENT_ID`.
- **First apply timing:** `terraform apply` may sit on the Container App for a few minutes while the placeholder image starts and the revision goes healthy. Subsequent applies are fast.
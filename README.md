# Terraform — Foundry Lab

Manages an Azure AI Foundry **project** + **Claude Haiku 4.5 deployment** under a pre-existing Foundry account (Microsoft.CognitiveServices, kind = `AIServices`). Agents are created out-of-band by `../agent/agent.py` because they're a data-plane construct, not an ARM resource.

```
providers.tf            azapi + azurerm provider pinning
backend.tf              azurerm remote state (Entra auth)
variables.tf            inputs + region validation
main.tf                 project + Claude Haiku deployment
outputs.tf              endpoints + IDs consumed by the agent step
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
```

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
- Push to `main` → `plan` + `apply`, then `agent.py` upserts the test agent against the freshly-applied project.

## Gotchas

- **Region:** Claude Haiku 4.5 only ships in `eastus2` and `swedencentral`. `variables.tf` enforces this.
- **`raiPolicyName = "Microsoft.Nill"`:** Claude has no Azure content filter. Add a Foundry content-safety policy before any external exposure.
- **A2A:** Foundry doesn't natively publish agents over A2A. `../agent/a2a_server.py` is the wrapper you register in Foundry Control Plane to expose the agent over A2A.
- **Model card:** Foundry has no first-class model-card resource. `../agent/model_card.md` is referenced via a `modelCard` tag on the deployment.

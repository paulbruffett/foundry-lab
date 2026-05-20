# Terraform — Foundry Lab

Manages an Azure AI Foundry **project** + **Claude Haiku 4.5 deployment** under a pre-existing Foundry account (Microsoft.CognitiveServices, kind = `AIServices`), plus the **A2A wrapper** (Container Apps + ACR) that publishes the agent for cross-platform consumption. The wrapper calls Foundry's native Anthropic Messages API directly — Foundry's Agents/Assistants service doesn't support Anthropic backing models, so there is no Foundry-side agent to upsert.

```
providers.tf            azapi + azurerm provider pinning
backend.tf              azurerm remote state (Entra auth)
variables.tf            inputs + region validation
main.tf                 Foundry project + Claude Haiku deployment
a2a.tf                  ACR + Container Apps + EasyAuth for the A2A wrapper
observability.tf        App Insights + project connection for tracing
outputs.tf              endpoints + IDs consumed by the agent + a2a steps
terraform.tfvars.example
```

For repo context and how to call the deployed agent, see the [root README](../README.md).

## Pre-terraform bootstrap

Everything in this section must be in place before `terraform apply` will succeed. Post-terraform manual steps (token acquisition, Foundry Control Plane registration) live in a separate section below.

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

# Foundry data plane — Terraform needs to hold this role to grant it to the
# A2A user-assigned identity at apply time (ABAC on Azure AI Project Manager
# blocks granting roles you don't yourself hold).
az role assignment create \
  --assignee-object-id "$SP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Azure AI Project Manager" \
  --scope "/subscriptions/$SUB_ID/resourceGroups/$FOUNDRY_RG"

# Foundry RG — required to grant the A2A user-assigned identity the Azure AI
# Project Manager role on the Foundry account at apply time. The "Azure AI
# Project Manager" role above includes roleAssignments/write but with an ABAC
# condition that blocks granting that very role to other principals.
az role assignment create \
  --assignee-object-id "$SP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "User Access Administrator" \
  --scope "/subscriptions/$SUB_ID/resourceGroups/$FOUNDRY_RG"

# A2A RG — Contributor needed for Container Apps + ACR + Log Analytics
A2A_RG="foundry-lab-a2a"
az group create -n "$A2A_RG" -l "$LOCATION"

az role assignment create \
  --assignee-object-id "$SP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Contributor" \
  --scope "/subscriptions/$SUB_ID/resourceGroups/$A2A_RG"

# A2A RG — required to grant the A2A user-assigned identity the AcrPull role
# on the ACR at apply time. Contributor alone doesn't include
# roleAssignments/write.
az role assignment create \
  --assignee-object-id "$SP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "User Access Administrator" \
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

EasyAuth rejects unauthenticated requests with `401`. The scope/preauthorization required for callers to actually mint tokens is set up after terraform — see the **Post-terraform setup** section below.

### 6. Register Azure resource providers

Fresh subscriptions don't have these registered, and Terraform 409s with `MissingSubscriptionRegistration` on the first apply.

```bash
for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.ContainerRegistry Microsoft.CognitiveServices Microsoft.SaaS; do
  az provider register --namespace "$ns"
done

# Poll until all show Registered before running terraform apply.
for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.ContainerRegistry Microsoft.CognitiveServices Microsoft.SaaS; do
  until [ "$(az provider show -n "$ns" --query registrationState -o tsv)" = "Registered" ]; do sleep 10; done
  echo "$ns registered"
done
```

### 7. Sign the Anthropic Marketplace agreement

Claude is delivered as a Models-from-Partners offer. The first deployment will fail until the agreement is accepted.

In the Azure portal: **Marketplace → search "Anthropic" → Subscribe**. One-time, per-subscription, and cannot be done with `az` alone.

### 8. GitHub repo configuration

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

After `apply` succeeds, the wrapper image still has to be built and rolled (CI does this on push to `main`; locally, mirror the `a2a` job below).

## CI behavior

`.github/workflows/terraform.yml` runs on every push and PR touching `terraform/`, `agent/`, or the workflow itself:

- PRs → `terraform plan` only.
- Push to `main` → two sequential jobs:
  1. **terraform** — `plan` + `apply` (Foundry project, Haiku deployment, ACR, Container Apps env, Container App, EasyAuth).
  2. **a2a** — `az acr build` builds the wrapper image from `../agent/Dockerfile`, pushes to ACR, then `az containerapp update --image` rolls the Container App. The job logs the public URL and the discovery-document path you paste into Foundry Control Plane.

The Container App is created with a public placeholder image (`nginxinc/nginx-unprivileged:alpine`, chosen because it listens on 8080 to match ingress) and `lifecycle.ignore_changes` on the image field — first apply will succeed even though the wrapper isn't built yet, and CI takes over from there.

## Post-terraform setup

After `terraform apply` succeeds and the `a2a` CI job rolls the wrapper image into the Container App, the endpoint is live but gated by EasyAuth. These steps are manual one-time actions to make the endpoint callable and registered with Foundry.

### 1. Expose an API scope and pre-authorize Azure CLI

Without this, `az account get-access-token --resource api://$A2A_APP_ID` returns `AADSTS65001` because Azure CLI has nothing on the audience app reg to consent to.

**Portal path** (recommended):

1. **Microsoft Entra ID → App registrations → `foundry-lab-a2a` → Expose an API**
2. Verify Application ID URI is `api://<A2A_APP_ID>` (already set in the bootstrap).
3. Click **Add a scope**:
   - Scope name: `user_impersonation`
   - Who can consent: **Admins and users**
   - Display names/descriptions: anything reasonable (e.g. "Access A2A")
   - State: Enabled
   - Save.
4. Click **Add a client application**:
   - Client ID: `04b07795-8ddb-461a-bbee-02f9e1bf7b46` (Microsoft Azure CLI's well-known appId)
   - Check the `user_impersonation` scope
   - Save.

**CLI equivalent** (if you'd rather script it):

```bash
A2A_APP_ID=$(az ad app list --display-name "foundry-lab-a2a" --query "[0].appId" -o tsv)
SCOPE_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
AZ_CLI_APP_ID="04b07795-8ddb-461a-bbee-02f9e1bf7b46"

cat > /tmp/api.json <<EOF
{
  "oauth2PermissionScopes": [{
    "id": "$SCOPE_ID",
    "adminConsentDescription": "Access the A2A wrapper",
    "adminConsentDisplayName": "Access A2A",
    "userConsentDescription": "Access the A2A wrapper on your behalf",
    "userConsentDisplayName": "Access A2A",
    "value": "user_impersonation",
    "type": "User",
    "isEnabled": true
  }],
  "preAuthorizedApplications": [{
    "appId": "$AZ_CLI_APP_ID",
    "delegatedPermissionIds": ["$SCOPE_ID"]
  }]
}
EOF

OBJECT_ID=$(az ad app show --id "$A2A_APP_ID" --query id -o tsv)
az rest --method PATCH \
  --url "https://graph.microsoft.com/v1.0/applications/$OBJECT_ID" \
  --headers "Content-Type=application/json" \
  --body "{\"api\": $(cat /tmp/api.json)}"
```

### 2. Register the endpoint with Foundry Control Plane

There is no clean Terraform/azapi resource for A2A registration today. Paste `https://<A2A_FQDN>/.well-known/agent-card.json` into **Foundry Control Plane → Connected agents → Add A2A endpoint** in the portal.

## Gotchas

- **Region:** Claude Haiku 4.5 only ships in `eastus2` and `swedencentral`. `variables.tf` enforces this.
- **No Azure content filter on Claude:** Anthropic's classifiers run server-side — Anthropic deployments don't accept `raiPolicyName`. Route through Azure AI Content Safety separately if you need extra filtering before/after the model call.
- **A2A auth:** the wrapper itself does no token validation; EasyAuth on the Container App ingress is the gate. Callers must present a bearer token whose audience is `api://$A2A_AAD_CLIENT_ID`.
- **First apply timing:** `terraform apply` may sit on the Container App for a few minutes while the placeholder image starts and the revision goes healthy. Subsequent applies are fast.

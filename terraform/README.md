# pick a globally-unique storage account name (3-24 chars, lowercase + digits)
SUB_ID="00000000-0000-0000-0000-000000000000"   # your subscription
RG="tfstate-foundry-lab-rg"
LOCATION="eastus2"
SA="tfstatefoundrylab$RANDOM"                    # must be globally unique
CONTAINER="tfstate"

az account set --subscription "$SUB_ID"

az group create -n "$RG" -l "$LOCATION"

az storage account create \
  -n "$SA" -g "$RG" -l "$LOCATION" \
  --sku Standard_LRS \
  --kind StorageV2 \
  --min-tls-version TLS1_2 \
  --allow-blob-public-access false

az storage container create \
  -n "$CONTAINER" \
  --account-name "$SA" \
  --auth-mode login

# record these — they go into GitHub repo *variables* (not secrets):
echo "TF_STATE_RESOURCE_GROUP   = $RG"
echo "TF_STATE_STORAGE_ACCOUNT  = $SA"
echo "TF_STATE_CONTAINER        = $CONTAINER"

# grant the GitHub Actions app reg write access to the state container
APP_OBJECT_ID="$(az ad sp show --id <AZURE_CLIENT_ID> --query id -o tsv)"
SCOPE="$(az storage account show -n "$SA" -g "$RG" --query id -o tsv)"
az role assignment create \
  --assignee-object-id "$APP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" \
  --scope "$SCOPE"
Notes:

The role assignment is what makes use_oidc = true in terraform/backend.tf work — without it, terraform init will 403 on the blob.
Replace <AZURE_CLIENT_ID> with the app registration's Application (client) ID — same value you'll put in the AZURE_CLIENT_ID GitHub secret.
Run these once, locally, with an account that has Owner on the subscription (or at least Contributor + User Access Administrator on the new RG).
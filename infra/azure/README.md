# Deploying Cabinet of Experts to Azure

Target topology (see `docs/ARCHITECTURE.md` §2):

| Component | Azure service |
|---|---|
| Backend API | Azure Container Apps (or AKS) |
| Frontend | Azure Container Apps / Static Web Apps |
| Database | Azure Database for PostgreSQL — Flexible Server |
| Secrets | Azure Key Vault |
| Skill/workspace storage | Azure Blob Storage |
| Real-time | Azure Web PubSub |
| LLM runtime | Microsoft Foundry (Azure AI) — Claude via `AnthropicFoundry` |

## 1. Provision

```bash
RG=cabinet-rg; LOC=westeurope
az group create -n $RG -l $LOC

# PostgreSQL Flexible Server
az postgres flexible-server create -g $RG -n cabinet-pg \
  --tier Burstable --sku-name Standard_B2s --version 16 \
  --database-name cabinet

# Key Vault + secrets (names must match infra/.env.example)
az keyvault create -g $RG -n cabinet-kv
az keyvault secret set --vault-name cabinet-kv -n foundry-api-key --value "<azure-ai-key>"
az keyvault secret set --vault-name cabinet-kv -n google-oauth-client-id --value "<client-id>"
az keyvault secret set --vault-name cabinet-kv -n google-oauth-client-secret --value "<client-secret>"
az keyvault secret set --vault-name cabinet-kv -n token-encryption-key --value "$(python3 -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
az keyvault secret set --vault-name cabinet-kv -n state-signing-key --value "$(openssl rand -base64 32)"
az keyvault secret set --vault-name cabinet-kv -n azure-blob-connection-string --value "<blob-conn>"
az keyvault secret set --vault-name cabinet-kv -n webpubsub-connection-string --value "<wps-conn>"

# Blob + Web PubSub
az storage account create -g $RG -n cabinetblob --sku Standard_LRS
az storage container create --account-name cabinetblob -n cabinet-skills
az webpubsub create -g $RG -n cabinet-wps --sku Free_F1

# Microsoft Foundry (Azure AI) — deploy a Claude model (serverless / MaaS)
# via Azure AI Studio; note the resource name for CABINET_FOUNDRY_RESOURCE.

# Entra ID app registrations for auth (CABINET_AUTH_MODE=entra):
#  - API app: exposes the scope the backend accepts as its token audience.
API_APP_ID=$(az ad app create --display-name cabinet-api \
  --sign-in-audience AzureADMyOrg --query appId -o tsv)
az ad app permission add --id $API_APP_ID # then expose "access_as_user" scope
#    (Azure Portal → App registrations → cabinet-api → Expose an API → Add a scope)
#  - SPA app: the frontend's public client, used by MSAL to sign users in and
#    request an access token for the API app's scope.
SPA_APP_ID=$(az ad app create --display-name cabinet-frontend \
  --sign-in-audience AzureADMyOrg \
  --spa-redirect-uris https://<frontend-fqdn> --query appId -o tsv)
az ad app permission add --id $SPA_APP_ID --api $API_APP_ID \
  --api-permissions <access_as_user-scope-id>=Scope
TENANT_ID=$(az account show --query tenantId -o tsv)
```

## 2. Deploy the API (Container Apps)

```bash
az containerapp env create -g $RG -n cabinet-env
az acr create -g $RG -n cabinetacr --sku Basic --admin-enabled true
az acr build -r cabinetacr -t cabinet-api:latest -f infra/Dockerfile.backend .
az acr build -r cabinetacr -t cabinet-frontend:latest -f infra/Dockerfile.frontend .

az containerapp create -g $RG -n cabinet-api \
  --environment cabinet-env \
  --image cabinetacr.azurecr.io/cabinet-api:latest \
  --ingress external --target-port 8000 \
  --system-assigned \
  --env-vars \
    CABINET_LLM_MODE=foundry \
    CABINET_FOUNDRY_RESOURCE=<ai-resource> \
    CABINET_FOUNDRY_AUTH=entra \
    CABINET_SECRETS_PROVIDER=azure_keyvault \
    CABINET_KEYVAULT_URL=https://cabinet-kv.vault.azure.net/ \
    CABINET_BLOB_PROVIDER=azure_blob \
    CABINET_REALTIME_PROVIDER=azure_webpubsub \
    CABINET_DATABASE_URL='postgresql+asyncpg://...' \
    CABINET_GOOGLE_REDIRECT_URI=https://<api-fqdn>/api/gdrive/callback \
    CABINET_AUTH_MODE=entra \
    CABINET_ENTRA_TENANT_ID=$TENANT_ID \
    CABINET_ENTRA_CLIENT_ID=$API_APP_ID
```

Frontend build (or Container App env, if the SPA is served dynamically) needs
the matching `VITE_AUTH_MODE=entra`, `VITE_ENTRA_TENANT_ID=$TENANT_ID`,
`VITE_ENTRA_CLIENT_ID=$SPA_APP_ID`, and
`VITE_ENTRA_API_SCOPE=api://$API_APP_ID/access_as_user`.

Grant the app's managed identity:

```bash
az keyvault set-policy -n cabinet-kv --object-id <app-mi-object-id> --secret-permissions get list
# Foundry access via Entra: assign "Cognitive Services User" on the AI resource.
```

## 3. Go-live checklist (mock → real credentials)

1. `CABINET_LLM_MODE=mock → foundry`; choose `api_key` (Key Vault) or `entra`.
2. `CABINET_SECRETS_PROVIDER=env → azure_keyvault` + `CABINET_KEYVAULT_URL`.
3. `CABINET_BLOB_PROVIDER=local → azure_blob`.
4. `CABINET_REALTIME_PROVIDER=inprocess → azure_webpubsub`.
5. Point `CABINET_DATABASE_URL` at the Flexible Server (sslmode=require).
6. Register the production redirect URI in the Google Cloud OAuth consent
   screen and update `CABINET_GOOGLE_REDIRECT_URI`.
7. `CABINET_AUTH_MODE=dev → entra` (+ `CABINET_ENTRA_TENANT_ID` /
   `CABINET_ENTRA_CLIENT_ID` from the API app registration above, and the
   matching `VITE_AUTH_MODE`/`VITE_ENTRA_*` frontend build vars) — swaps the
   dev `X-User-Email` header for verified Microsoft Entra ID JWTs
   (`app/services/entra_auth.py`); no other code changes.

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
    CABINET_GOOGLE_REDIRECT_URI=https://<api-fqdn>/api/gdrive/callback
```

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
7. Replace the dev `X-User-Email` header identity (`app/api/deps.py`) with
   Microsoft Entra ID JWT validation.

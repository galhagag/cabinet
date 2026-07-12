# Cabinet of Experts

Enterprise multi-agent collaboration chat system that streamlines onboarding of
new financial institutions onto the **ThetaRay Transaction Monitoring** platform.

Each customer gets a **Cabinet Room** where two Claude-powered experts —
a **Data Expert** (data science/engineering) and a **Financial Crime Expert**
(AML/compliance) — collaborate autonomously and with human stakeholders in
real time.

📐 Full design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

## Highlights

- **Azure-native**: Claude via **Microsoft Foundry** (`AnthropicFoundry` SDK,
  API key or Entra ID), Azure Database for **PostgreSQL**, **Key Vault**,
  **Blob Storage**, **Web PubSub**, deployable to **Container Apps/AKS**.
- **Global admin config** for both expert baseline prompts; per-room
  "UI-enriched" context that **appends and never overwrites** the baseline.
- **Agent-to-agent loop budget**: autonomous exchanges hard-cap at
  **6 cycles**, then the room pauses awaiting human input.
- **@-mentions** (`@DataExpert`, `@FCE`) route a message exclusively to the
  tagged agent for one targeted, history-aware reply.
- **Google Drive OAuth2** (real authorization-code lifecycle, Fernet-encrypted
  tokens, auto-refresh) to link Drive folders to a room.
- **Microsoft Entra ID auth**: JWT access tokens verified against the
  tenant's JWKS (no shared secret); MSAL sign-in on the frontend. Dev/test
  keeps a trusted `X-User-Email` header instead — flip `CABINET_AUTH_MODE`.
- **Dynamic skills**: upload `.md` docs or `.zip` bundles (with `SKILL.md`) to
  extend an agent's capabilities at runtime; stored in Blob Storage.
- **Multi-user rooms** with secure, expiring invite links and live WebSocket
  streaming of the agent collaboration.

## Quick start (dev, fully mocked — no credentials needed)

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload            # http://localhost:8000  (API + WS)

# Frontend
cd frontend
npm install
npm run dev                              # http://localhost:5173 (proxies /api,/ws)

# Tests
cd backend && python -m pytest tests -q
```

Dev mode runs `CABINET_LLM_MODE=mock` (deterministic scripted agents), local
blob storage, env-var secrets, in-process WebSockets, and `CABINET_AUTH_MODE=dev`
(trusted `X-User-Email` header). Flip the environment variables in
[`infra/.env.example`](infra/.env.example) to go live on Azure — including
`CABINET_AUTH_MODE=entra` (+ `CABINET_ENTRA_TENANT_ID`/`CABINET_ENTRA_CLIENT_ID`
and the matching `VITE_ENTRA_*` frontend vars) for real Microsoft Entra ID
sign-in — see [`infra/azure/README.md`](infra/azure/README.md) for the full
deployment and go-live checklist.

## Repository layout

```
backend/    FastAPI API, agent orchestrator, Azure service connectors, tests
frontend/   React + Vite + TypeScript multi-agent room UI
docs/       Architecture & design document
infra/      Dockerfiles, docker-compose, Azure deployment notes, env template
```

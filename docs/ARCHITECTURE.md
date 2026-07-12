# Cabinet of Experts — System Design Document

**Multi-Agent Collaboration Chat System for ThetaRay Customer Onboarding**

Version 1.0 · Azure-Native Architecture

---

## 1. Purpose & Scope

Cabinet of Experts streamlines the onboarding of new financial institutions (banks,
FinTechs, PSPs) onto the ThetaRay Transaction Monitoring platform. Each customer
onboarding gets a dedicated virtual workspace — a **Cabinet Room** — where two
domain-expert AI agents collaborate with each other and with human stakeholders:

| Agent | Key | Domain |
|---|---|---|
| **Data Expert** | `data_expert` | Data science & data engineering: schema mapping, Parquet/MinIO ingestion validation, feature catalogs, Spark executor sizing, MLFlow training pipelines |
| **Financial Crime Expert (FCE)** | `fce` | AML/Compliance: risk assessment workshops, detection rule metrics (rolling windows, credit-transaction rules, country whitelists), alert lifecycle, 1LOD/2LOD Investigation Center workflows, MRM templates |

Agents converse autonomously (bounded by a strict **6-cycle loop budget**), accept
runtime skill uploads, and can be targeted directly via `@DataExpert` / `@FCE`
mentions. Humans join rooms via secure invite links and steer the conversation in
real time.

---

## 2. Azure-Native Architecture Overview

```
                        ┌────────────────────────────────────────────────┐
                        │                Azure Front Door / App Gateway   │
                        └───────────────┬────────────────────────────────┘
                                        │ HTTPS / WSS
        ┌───────────────────────────────┼───────────────────────────────────┐
        │            Azure Container Apps (ACA) — or AKS                    │
        │  ┌─────────────────────┐        ┌──────────────────────────────┐  │
        │  │  Frontend (React/    │        │  Backend API (FastAPI)       │  │
        │  │  Vite, static via    │◀──────▶│  · REST routers              │  │
        │  │  nginx container)    │        │  · WebSocket hub             │  │
        │  └─────────────────────┘        │  · Agent Orchestrator        │  │
        │                                  └───────┬──────────┬───────────┘  │
        └──────────────────────────────────────────┼──────────┼──────────────┘
                                                   │          │
                 ┌─────────────────────────────────┤          ├──────────────────────────┐
                 │                    │            │          │              │           │
                 ▼                    ▼            ▼          ▼              ▼           ▼
      ┌──────────────────┐ ┌──────────────┐ ┌──────────┐ ┌─────────────┐ ┌─────────┐ ┌──────────────┐
      │ Microsoft Foundry │ │ Azure DB for │ │ Azure    │ │ Azure Blob  │ │ Azure   │ │ Google Drive │
      │ (Claude MaaS via  │ │ PostgreSQL   │ │ Key Vault│ │ Storage     │ │ Web     │ │ API (OAuth2) │
      │ AnthropicFoundry  │ │ Flexible Srv │ │          │ │ (skills +   │ │ PubSub /│ │              │
      │ SDK client)       │ │              │ │          │ │  workspace) │ │ SignalR │ │              │
      └──────────────────┘ └──────────────┘ └──────────┘ └─────────────┘ └─────────┘ └──────────────┘
```

### 2.1 Service Responsibilities

| Azure Service | Role in Cabinet |
|---|---|
| **Microsoft Foundry (Azure AI Studio, Claude MaaS)** | Runs all agent inference. Accessed exclusively through the official `AnthropicFoundry` / `AsyncAnthropicFoundry` SDK clients (`anthropic` Python package). Auth: Azure AI API key **or** Microsoft Entra ID via `azure_ad_token_provider`. |
| **Azure Container Apps / AKS** | Hosts the stateless backend API + frontend containers. Dockerfiles + compose provided under `infra/`. |
| **Azure Database for PostgreSQL (Flexible Server)** | System of record: rooms, messages (audit trail), agent configs, memberships, invites, Google Drive token state, skill registry. Accessed via SQLAlchemy 2.0 async + `asyncpg`. Tests run the identical models on SQLite/aiosqlite. |
| **Azure Key Vault** | Holds Google OAuth client credentials, Foundry API key, Postgres connection string, token-encryption key. Abstracted behind a `SecretProvider` interface; a `MockSecretProvider` (env/file-backed) is used in dev/test and swapped for `AzureKeyVaultSecretProvider` in production. |
| **Azure Blob Storage** | Skill uploads (`.md`, `.zip` bundles) and workspace folder sync targets. Abstracted behind a `BlobStorageProvider`; `LocalBlobStorageProvider` for dev/test, `AzureBlobStorageProvider` for production. |
| **Azure Web PubSub / SignalR** | Production fan-out for real-time room streams. The backend publishes through a `RealtimeBroker` interface; dev/test uses the in-process WebSocket `ConnectionManager`, production plugs the Azure Web PubSub broadcaster into the same interface. |

### 2.2 Mock-to-Production Credential Strategy

All external dependencies sit behind provider interfaces selected by configuration
(`app/config.py`). Development and CI run with mocks; production flips env vars —
no code changes:

| Setting | Dev / Test | Production |
|---|---|---|
| `CABINET_LLM_MODE` | `mock` (deterministic scripted agents) | `foundry` |
| `CABINET_SECRETS_PROVIDER` | `env` | `azure_keyvault` (+ `CABINET_KEYVAULT_URL`) |
| `CABINET_BLOB_PROVIDER` | `local` | `azure_blob` (+ connection secret from Key Vault) |
| `CABINET_REALTIME_PROVIDER` | `inprocess` | `azure_webpubsub` |
| `CABINET_DATABASE_URL` | `sqlite+aiosqlite:///...` | `postgresql+asyncpg://...` (from Key Vault) |
| `CABINET_AUTH_MODE` | `dev` (trusted `X-User-Email` header) | `entra` (Microsoft Entra ID JWT, verified against tenant JWKS) |

`infra/.env.example` documents every variable with the exact secret names expected
in Key Vault (`google-oauth-client-id`, `google-oauth-client-secret`,
`foundry-api-key`, `postgres-connection-string`, `token-encryption-key`).

---

## 3. Repository Layout

```
cabinet/
├── docs/
│   └── ARCHITECTURE.md              ← this document
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py                  # FastAPI app factory; router + WS mounting
│   │   ├── config.py                # Settings; provider-mode switches
│   │   ├── schemas.py               # Pydantic API contracts
│   │   ├── db/
│   │   │   ├── base.py              # Declarative base + async engine/session
│   │   │   └── models.py            # ORM models (schema §4)
│   │   ├── agents/
│   │   │   ├── profiles.py          # Baseline system prompts (Data Expert, FCE)
│   │   │   ├── prompt_compiler.py   # baseline ⊕ skills ⊕ room enrichment (append-only)
│   │   │   ├── foundry_client.py    # AnthropicFoundry wrapper + deterministic mock
│   │   │   └── orchestrator.py      # A2A loop w/ 6-cycle budget, @mention routing
│   │   ├── services/
│   │   │   ├── secrets.py           # SecretProvider: Azure Key Vault | env mock
│   │   │   ├── blob_storage.py      # BlobStorageProvider: Azure Blob | local FS
│   │   │   ├── realtime.py          # RealtimeBroker: Web PubSub | in-process WS hub
│   │   │   ├── google_oauth.py      # Real OAuth2 code flow + refresh + encryption
│   │   │   └── skills.py            # .md/.zip skill ingestion → blob + registry
│   │   └── api/
│   │       ├── admin.py             # GET/PUT global baseline prompts
│   │       ├── rooms.py             # room CRUD, invites, join, members
│   │       ├── messages.py          # post message, history, resume-after-pause
│   │       ├── gdrive.py            # OAuth authorize/callback/status/link
│   │       ├── skills.py            # skill upload endpoints
│   │       └── ws.py                # /ws/rooms/{room_id} live stream
│   └── tests/                       # pytest integration suite (§8)
├── frontend/
│   ├── package.json / vite.config.ts / tsconfig.json / index.html
│   └── src/
│       ├── api.ts                   # typed REST client
│       ├── ws.ts                    # room WebSocket client (auto-reconnect)
│       ├── App.tsx                  # router: lobby / room / admin
│       └── components/
│           ├── RoomList.tsx         # lobby: create room (customer name + enrichment)
│           ├── RoomView.tsx         # multi-agent room: thread + live status
│           ├── ChatThread.tsx       # message stream w/ agent identity styling
│           ├── Composer.tsx         # input w/ @DataExpert / @FCE mention picker
│           ├── LoopBudgetBanner.tsx # cycles-used meter + paused-state resume UX
│           ├── AdminPanel.tsx       # global baseline prompt editor
│           ├── SkillUploadDialog.tsx# .md / .zip upload per agent
│           ├── DrivePanel.tsx       # Google Drive OAuth + folder linking
│           └── InviteDialog.tsx     # secure share-link generation
└── infra/
    ├── Dockerfile.backend
    ├── Dockerfile.frontend
    ├── docker-compose.yml           # api + frontend + postgres for local prod-parity
    ├── .env.example                 # all env vars + Key Vault secret names
    └── azure/README.md              # ACA deployment notes (az CLI commands)
```

---

## 4. Database Schema (PostgreSQL Flexible Server)

SQLAlchemy 2.0 declarative models; identical DDL runs on SQLite for tests.

```
agent_global_config          rooms                          room_agents
─────────────────────        ─────────────────────          ──────────────────────
agent_key      PK            id             PK (uuid)       id           PK (uuid)
display_name                 customer_name  UNIQUE          room_id      FK rooms
system_prompt  TEXT          enrichment_prompt TEXT NULL    agent_key    FK config
updated_at                   status  ENUM(active|           display_name
                                     paused_awaiting_human) created_at
                             cycles_used    INT DEFAULT 0
                             cycle_limit    INT DEFAULT 6
                             created_by
                             created_at

messages                     room_members                   room_invites
─────────────────────        ─────────────────────          ──────────────────────
id            PK (uuid)      id          PK (uuid)          token       PK (secure)
room_id       FK rooms       room_id     FK rooms           room_id     FK rooms
sender_type   ENUM(human|    user_email  (uniq per room)    created_by
              agent|system)  display_name                   expires_at
sender_name                  role ENUM(owner|member)        created_at
agent_key     NULL           joined_at
mention_target NULL
cycle_number  NULL
content       TEXT
created_at    (indexed)

gdrive_connections           agent_skills                   audit_log
─────────────────────        ─────────────────────          ──────────────────────
id            PK (uuid)      id          PK (uuid)          id          PK (bigint)
room_id       FK rooms(uniq) room_id     FK rooms NULL      room_id     NULL
google_folder_id  NULL         (NULL ⇒ global skill)        actor
google_folder_name NULL      agent_key                      action
access_token_enc  TEXT       skill_name                     detail JSON
refresh_token_enc TEXT       skill_type  ENUM(md|zip)       created_at
token_expiry  TIMESTAMPTZ    blob_path   (Azure Blob key)
scopes        TEXT           content_text TEXT (md body /
status ENUM(pending|linked|               zip SKILL.md body)
       error|revoked)        created_at
created_at / updated_at
```

Notes:

- **Audit trail** — every message row is immutable; `audit_log` additionally records
  admin prompt changes, room lifecycle events, OAuth link/unlink, and skill uploads.
- **Token security** — Google access/refresh tokens are Fernet-encrypted at rest with
  a key held in Azure Key Vault (`token-encryption-key`); rows never store plaintext.
- **`cycle_limit`** is stored per room (default 6) so the platform team can tune a
  room without redeploying, while the product default enforces the spec.

---

## 5. Agent Runtime (Microsoft Foundry / Claude)

### 5.1 Client

```python
from anthropic import AsyncAnthropicFoundry

client = AsyncAnthropicFoundry(
    resource=settings.foundry_resource,        # e.g. "thetaray-cabinet"
    api_key=settings.foundry_api_key,          # from Key Vault, OR:
    # azure_ad_token_provider=get_bearer_token_provider(
    #     DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
)
response = await client.messages.create(
    model=settings.foundry_model,              # default "claude-opus-4-8"
    max_tokens=settings.agent_max_tokens,
    system=compiled_system_prompt,
    messages=conversation_window,
)
```

- `FoundryLLM` implements the `LLMBackend` protocol; `MockLLM` implements the same
  protocol with deterministic, domain-flavored scripted replies so the entire
  system (and CI) runs without network access or credentials.
- Note: server-side refusal fallbacks are unavailable on Foundry; the wrapper
  therefore checks `stop_reason == "refusal"` and degrades to a polite in-room
  system message.

### 5.2 Prompt Compilation — Enrich, Never Overwrite

```
compiled_system_prompt =
    GLOBAL_BASELINE[agent_key]                      (admin-editable, always first)
  + "\n\n## Acquired Skills\n" + skill_sections      (only if skills exist)
  + "\n\n## Room Context Enrichment\n" + enrichment  (only if room has one)
```

The compiler is a pure function (`compile_system_prompt`) with the invariant —
enforced by tests — that the output **starts with the unmodified global baseline**;
UI-supplied enrichment can only ever append.

### 5.3 Agent-to-Agent Loop Control (6-Cycle Budget)

```
human message (or resume) ──▶ orchestrator.run_autonomous_loop(room)
     │
     ▼
 ┌─────────────────────────────────────────────────────────┐
 │ while room.cycles_used < room.cycle_limit:              │
 │     speaker  = next agent (alternating, or mentioned)   │
 │     reply    = LLM(compiled_prompt(speaker), history)   │
 │     persist message(cycle_number=cycles_used+1)         │
 │     broadcast over RealtimeBroker                       │
 │     room.cycles_used += 1                               │
 │     if reply signals completion ("HANDOFF_TO_HUMAN")    │
 │         break                                           │
 │ if budget exhausted:                                    │
 │     room.status = "paused_awaiting_human"               │
 │     broadcast room_paused event                         │
 └─────────────────────────────────────────────────────────┘
     │
     ▼
 next human message ⇒ cycles_used reset to 0, status → active, loop may resume
```

- A **cycle** = one agent turn in the autonomous exchange. `cycle_limit=6` caps a
  runaway exchange at 6 agent turns before a hard pause.
- `@DataExpert` / `@FCE` mentions bypass the loop: the message routes **only** to the
  tagged agent, which produces exactly one targeted reply (1 cycle) using the full
  room history window.
- Pause state is visible in the UI (`LoopBudgetBanner`) and enforced server-side —
  agents cannot speak in a paused room until a human posts.
- **Concurrency safety**: each cycle is claimed with an atomic conditional
  `UPDATE … WHERE status='active' AND cycles_used < cycle_limit RETURNING`, so
  overlapping requests (or replicas) share one budget and can never exceed the
  cap; the paused→active transitions (human message, Resume) are equally atomic.
- The explicit **Resume** control is a deliberate human action that grants a
  fresh budget without new message content; it is subject to the same atomic
  transition (concurrent clicks: one wins, the rest get 409).

### 5.4 Mention Routing

`parse_mention()` recognizes `@DataExpert`, `@FCE` (case-insensitive, plus
`@data_expert`/`@fce` aliases). The orchestrator compiles the surrounding chat
history (bounded window, most-recent-first truncation) and requests a single
domain-specific response from the tagged agent only.

---

## 6. Google Drive OAuth2 (Production Lifecycle)

```
 UI "Connect Drive"                Backend                      Google
 ──────────────────                ───────                      ──────
 GET /rooms/{id}/gdrive/authorize ─▶ build consent URL          
     ◀── { authorize_url, state } ── (client_id from Key Vault,
                                      state=signed room ref)
 browser redirect ────────────────────────────────────────────▶ consent screen
                                                               ◀ redirect w/ code
 GET /gdrive/callback?code&state ─▶ verify state (HMAC-signed)
                                    POST oauth2.googleapis.com/token
                                    encrypt tokens (Fernet) → gdrive_connections
 POST /rooms/{id}/gdrive/folder  ─▶ store folder id; list via Drive API
 (refresh)                        ─▶ auto-refresh w/ refresh_token when expired
```

- Scopes: `https://www.googleapis.com/auth/drive.readonly` (folder sync is read-only
  into the workspace).
- `state` is HMAC-signed (`itsdangerous`) binding the flow to a room + user; callback
  rejects tampered or expired states.
- Google token endpoints are called through `httpx`; in tests the transport is
  swapped for a `MockTransport` so the **full code path** (state verify → token
  exchange → encryption → persistence → refresh) is exercised without network.

---

## 7. Real-Time Layer

- Dev/in-process: FastAPI WebSocket endpoint `/ws/rooms/{room_id}`; a
  `ConnectionManager` keeps per-room connection sets and fans out JSON events:
  `message_created`, `agent_thinking`, `room_paused`, `room_resumed`,
  `skill_added`, `drive_linked`.
- Production: `AzureWebPubSubBroker` implements the same `RealtimeBroker.publish()`
  interface using the `azure-messaging-webpubsubservice` SDK (group = room id);
  clients connect to Web PubSub with a server-issued client access token.

---

## 8. Test Strategy (Gate 4)

Integration tests run the real FastAPI app over ASGI (`httpx.AsyncClient`) against
SQLite/aiosqlite with `CABINET_LLM_MODE=mock`:

| Test module | Requirement verified |
|---|---|
| `test_admin_config.py` | Global baseline prompt read/update; audit entry |
| `test_rooms.py` | Room creation spins up both agents; invites; join via token |
| `test_prompt_enrichment.py` | Compiled prompt = baseline prefix + appended enrichment; never overwritten |
| `test_loop_budget.py` | Autonomous loop halts at exactly 6 cycles; room pauses; human message resumes/resets |
| `test_mentions.py` | `@FCE` routes only to FCE; `@DataExpert` only to Data Expert; single reply |
| `test_gdrive_oauth.py` | Authorize URL, signed state, code exchange, encrypted persistence, refresh |
| `test_skills_upload.py` | `.md` upload extends compiled prompt; `.zip` with SKILL.md ingested; blob stored |
| `test_websocket.py` | WS clients receive `message_created` / `room_paused` events |

A clean-context verifier subagent audits the finished codebase against: loop
budget enforcement, Key Vault/Postgres patterns, prompt-enrichment invariant, and
Foundry (Claude Messages API) readiness — before completion is declared.

---

## 9. Security Posture

- Secrets only via `SecretProvider` (Key Vault in prod); no credentials in code or DB.
- Google tokens encrypted at rest (Fernet); encryption key itself in Key Vault.
- Invite tokens: 32-byte URL-safe random, expiring, single-room scope.
- OAuth `state` HMAC-signed and time-limited.
- All mutating endpoints write `audit_log` rows (regulated-industry traceability).
- WebSocket join requires room membership (email header in dev; Entra ID JWT in prod
  — the dependency is isolated in `api/deps.py` for a one-line swap).

### 9.1 Authentication (`CABINET_AUTH_MODE`)

- **`dev`** (default, dev/test only): caller identity comes straight from the
  `X-User-Email` header, trusted with zero verification. The frontend lets a
  user type any email into a text box. Never set in production.
- **`entra`**: caller identity comes from a Microsoft Entra ID (Azure AD v2.0)
  access token, presented as `Authorization: Bearer <token>` on HTTP requests
  and as an `?access_token=` query parameter on the WebSocket handshake
  (browsers cannot set custom headers on a WS upgrade). `EntraTokenValidator`
  (`backend/app/services/entra_auth.py`) verifies, per request:
  - **Signature** — RS256 against the tenant's JWKS
    (`https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys`),
    fetched lazily and refetched on an unrecognized `kid` (handles Entra's
    routine key rotation) — no shared secret ever reaches the backend.
  - **Issuer** — `https://login.microsoftonline.com/{tenant}/v2.0`.
  - **Audience** — the API app registration's client ID
    (`CABINET_ENTRA_CLIENT_ID`), distinct from the frontend SPA's client ID.
  - **Expiry** — standard `exp`/`iat` validation.
  - The caller's email is read from the token's verified
    `preferred_username` / `email` / `upn` claim — never from a client-
    supplied header.
  - Required config: `CABINET_ENTRA_TENANT_ID`, `CABINET_ENTRA_CLIENT_ID`.
  - Frontend: `frontend/src/auth.ts` wraps `@azure/msal-browser`
    (`PublicClientApplication`) for the redirect sign-in flow and silent
    token acquisition/refresh; gated by `VITE_AUTH_MODE=entra` plus
    `VITE_ENTRA_TENANT_ID` / `VITE_ENTRA_CLIENT_ID` / `VITE_ENTRA_API_SCOPE`.
  - `get_current_user_email` (`api/deps.py`) is the single dependency every
    router uses for identity — everything downstream (room membership,
    admin allowlist, audit log `actor`) is unchanged between modes.
  - Tests: `backend/tests/test_entra_auth.py` exercises the full validation
    path (good token, expired, wrong audience, wrong issuer, tampered
    signature, unknown `kid`) against a self-signed RSA keypair over
    `httpx.MockTransport` — no real Azure AD tenant needed to verify the
    code path; only your real tenant/client IDs are needed to go live.

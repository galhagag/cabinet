# Design 08 — Secrets & OAuth Key Management

**Status:** Proposed
**Addresses:** H10 (live-shaped credentials in `infra/.env`; weak Postgres
password on a public endpoint), M2 (`EnvSecretProvider` generates ephemeral
per-process crypto keys when env vars are blank → undecryptable Drive tokens,
cross-replica OAuth-state failure).
**Effort:** S (≤2 days) + a credential-rotation operational task

**Phase 2 progress:** M2 shipped in `fix/secrets-key-stability-08` —
`EnvSecretProvider` now raises instead of generating an ephemeral key for
`token-encryption-key`/`state-signing-key` when `CABINET_ENV != dev`, and
`GoogleOAuthService` uses a `MultiFernet` keyring (primary +
`token-encryption-key-previous`) so rotating the encryption key doesn't
orphan existing Drive tokens. **H10 (actually rotating the leaked Google
OAuth secret, Azure AI key, and Postgres password) is NOT done and cannot be
done by an agent in this environment** — it requires access to Google Cloud
Console, Azure Key Vault, and the production Postgres server. This remains
an outstanding manual/operational task for the team. Item 4 (confirming the
Container App runs with a managed identity) is also unchanged — it's an
Azure infra configuration check, not code.

---

## Problem

### M2 — ephemeral crypto keys (the sharp edge)

`EnvSecretProvider.get_secret` treats an empty env var as unset and falls
through to `_dev_default` ([secrets.py:53](../../backend/app/services/secrets.py#L53)),
which for `token-encryption-key` does `Fernet.generate_key()` and for
`state-signing-key` does `token_urlsafe(32)` — cached per process only
([secrets.py:29](../../backend/app/services/secrets.py#L29)). The tracked
`infra/.env` leaves `CABINET_SECRET_TOKEN_ENCRYPTION_KEY=` and
`CABINET_SECRET_STATE_SIGNING_KEY=` blank. Consequences:

- Every backend **restart** generates a new Fernet key, so Google Drive tokens
  encrypted before the restart become **permanently undecryptable** — the Drive
  link silently breaks and refresh fails.
- Every **replica** has a different key, so OAuth `state` signed on replica A
  fails HMAC verification on replica B — intermittent "invalid state" callback
  failures under load.

This is a correctness *and* security problem: it looks like it works in a
single-process dev run and fails only in the exact multi-replica/restart
conditions production runs in.

### H10 — credentials at rest

`infra/.env` (correctly git-ignored and never committed — verified) holds
live-shaped secrets: a Postgres URL with password `P@ssw0rd` on a public Azure
endpoint (brute-forceable), a real Google OAuth client secret, and an Azure AI
key reused for the Anthropic-Foundry and Azure-OpenAI backends. Not a repo leak,
but plaintext on disk one `git add -f`/backup/screen-share from exposure, with a
trivially guessable DB password.

## Goals

- Crypto keys are **stable** across restarts and replicas in every non-dev
  environment.
- Production never silently runs on generated ephemeral keys.
- The currently-exposed credentials are rotated and the DB password is strong.
- Dev stays zero-config.

## Design

### 1. Fail loudly instead of generating prod keys (M2)

- In **dev** (`CABINET_ENV=dev`, see [Design 01](01-fail-closed-production-config.md)):
  keep generated defaults, but **persist them** for the dev machine (write once
  to a git-ignored `.dev-secrets.json` under the blob root) so restarts reuse
  the same key and Drive tokens survive local restarts. Log a clear
  "using generated dev key" line.
- In **staging/prod**: `EnvSecretProvider._dev_default` must **raise** for
  `token-encryption-key` and `state-signing-key` (and any secret) rather than
  generate — surfaced by the Design 01 boot guard, which already requires
  `secrets_provider=azure_keyvault` in prod unless the explicit
  `CABINET_ALLOW_ENV_SECRETS=1` escape hatch is set. If env secrets are
  explicitly allowed, a blank crypto key is still a hard error.
- Treat blank-string env vars as **unset with intent to fail**, not "use a
  default": distinguish "not configured" (dev default OK where allowed) from
  "configured empty" only by environment policy, never by silently minting keys
  in prod.

### 2. Key rotation support

Fernet supports multi-key rotation (`MultiFernet`). Introduce
`token-encryption-key` as the *primary* plus an optional
`token-encryption-key-previous`, decrypt with either, encrypt with the primary.
This lets ops rotate the encryption key in Key Vault without orphaning existing
Drive tokens — and is the correct answer to "we generated ephemeral keys and now
have undecryptable rows": rotate forward, re-link affected rooms.

### 3. Rotate the exposed credentials (H10)

Operational, not code:

- Rotate the Google OAuth client secret in Google Cloud Console; update Key
  Vault (`google-oauth-client-secret`).
- Rotate the Azure AI key; **split** into distinct secrets per backend
  (`foundry-api-key` vs `azure-openai-api-key`) so one can be rotated
  independently.
- Reset the Postgres admin password to a strong, generated value; store the
  connection string in Key Vault (`postgres-connection-string`) and stop putting
  it in `infra/.env`. Restrict the Postgres firewall to the app's VNet/subnet
  rather than a public endpoint.
- Scrub `infra/.env` down to *non-secret* dev toggles; move every real secret to
  `CABINET_SECRET_*` referencing Key Vault names, and document per-developer dev
  secrets (personal test Google app, local throwaway DB password).

### 4. Managed identity in prod

`AzureKeyVaultSecretProvider` already uses `DefaultAzureCredential`
([secrets.py:74](../../backend/app/services/secrets.py#L74)) — confirm the
Container App runs with a **managed identity** granted `get` on the vault, so no
bootstrap secret is needed to reach Key Vault (the one credential you can't put
in Key Vault).

## Implementation sketch

- `services/secrets.py`: environment-aware `_dev_default` (raise in prod;
  persist in dev); `MultiFernet` helper for encryption with primary+previous
  keys; treat blank per policy.
- `services/google_oauth.py`: use the rotation-aware decrypt.
- `config.py`: `CABINET_ALLOW_ENV_SECRETS`, `token-encryption-key-previous`
  secret name.
- `infra/.env.example` + `infra/.env`: strip secrets; add a rotation checklist.
- `infra/azure/README.md`: managed-identity + Key Vault access-policy steps;
  Postgres firewall/VNet note.

## Testing

- `test_secrets.py`: in prod env with a blank crypto key, `get_secret` raises
  (not generate); in dev, two sequential provider instances return the *same*
  persisted key.
- `test_gdrive_oauth.py`: encrypt with key A, rotate to key B (A as previous),
  assert existing token still decrypts and new tokens use B.
- Boot guard (Design 01) test covers "prod + env secrets + blank key → refuse to
  boot".

## Rollout & risks

- **Do the rotation regardless of the code changes** — treat the current
  `infra/.env` credentials as burned.
- The dev-key-persistence change is optional but removes a genuine local
  annoyance (Drive re-link after every restart).
- `MultiFernet` rotation is additive and backward-compatible.

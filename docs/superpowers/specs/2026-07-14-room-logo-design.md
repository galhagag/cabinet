# Room Logo — Design Spec

## Goal

Give each Cabinet room a visual identity based on the financial institution
it represents, instead of the generic agent/member avatar cluster. By
default the logo is looked up automatically from the room's `customer_name`;
a room member can always override it with their own uploaded image.

## Non-goals

- Reverting a custom upload back to the auto-fetched logo. `logo_source`
  becomes `"custom"` permanently once someone uploads an image — if this
  turns out to be needed, it's a small follow-up (re-run the lookup, same
  code path as creation).
- Refreshing a room's logo after the institution rebrands. The lookup runs
  once, right after room creation.
- Live-updating a room's logo in the sidebar while a *different* room is
  open. See "Accepted limitation" below.
- SVG upload support. PNG/JPEG/WebP only, to avoid the sanitization
  question SVG-as-`<img>` sources otherwise raise.

## Data model

`Room` (`backend/app/db/models.py`) gains two columns:

```
logo_blob_path  String, nullable      # blob storage key, e.g. "rooms/<id>/logo.png"
logo_source     String, default "pending"   # "pending" | "auto" | "custom" | "none"
```

New Alembic migration, additive only — same shape as the two existing
additive migrations already in `backend/alembic/versions/`.

## Lookup flow

`POST /api/rooms` creates the `Room` row exactly as it does today
(`logo_source="pending"` by default), then schedules a FastAPI
`BackgroundTasks` job — no new job-queue infrastructure — that:

1. Calls the Brandfetch Brand Search API with `customer_name`, resolving it
   to a domain + logo asset. New secret `brandfetch-api-key`, resolved
   through the existing `SecretProvider` (Key Vault in prod), mirroring the
   `tavily_api_key_secret` convention already in `config.py`.
2. Downloads the logo image bytes via `httpx`.
3. Uploads them to blob storage at `rooms/{room_id}/logo.<ext>` via the
   existing `BlobStorageProvider.upload()`.
4. Updates the room row: `logo_blob_path` set, `logo_source="auto"`.
5. Broadcasts `room_logo_updated` over the room's WS channel and writes an
   `AuditLog` row (`room_logo_fetched`).

If Brandfetch finds nothing, or any step fails (timeout, 4xx, decode
error), the job sets `logo_source="none"` and returns — it never raises
into the request/response cycle (room creation has already returned by the
time this runs) and never leaves a room stuck in `"pending"` forever.

## API changes

- **`GET /api/rooms/{room_id}/logo`** *(new)* — streams the blob's bytes
  with the appropriate `Content-Type`, gated by `require_room_member` like
  every other room-scoped read. Used directly as an `<img src>`.
- **`POST /api/rooms/{room_id}/logo`** *(new)* — multipart upload, modeled
  directly on `skills.py`'s existing `POST .../skills` endpoint (`file:
  UploadFile` param, sanitized path, `blob.upload()`, `AuditLog` row).
  Validates `Content-Type` is one of `image/png`, `image/jpeg`,
  `image/webp`, and rejects bodies over 2MB. Sets `logo_blob_path` to the
  new path and `logo_source="custom"`. Audit-logged as `room_logo_uploaded`.
- `RoomOut` gains `logo_url: str | null` — `/api/rooms/{id}/logo` when
  `logo_blob_path` is set, else `null` — and `logo_source`.

## Real-time & audit

- New WS event `room_logo_updated` — `{room_id, logo_url, logo_source}`,
  broadcast via the existing `broker.publish(room_id, {...})` used by
  `drive_linked` — fired after both the background fetch and a manual
  upload.
- New `AuditLog` actions: `room_logo_fetched` (`{logo_source}`) and
  `room_logo_uploaded` (`{uploaded_by}`).

## Frontend

- New `RoomLogo` component (`frontend/src/components/RoomLogo.tsx`):
  renders `<img src={logo_url}>` when the room has one, otherwise the same
  initials-avatar treatment `Avatar.tsx` already computes for people —
  reusing `initialsFor()` and the color-hash palette against
  `customer_name` — so a room with no logo yet (or none found) still reads
  as a distinct, colored identity, not a blank space.
- `RoomView.tsx` header: `<AvatarCluster items={clusterItems} .../>` is
  replaced with `<RoomLogo room={room} size={40} editable />`. `editable`
  renders a pencil-icon overlay on hover that opens a file picker; picking
  a file calls a new `uploadRoomLogo(roomId, file)` (`frontend/src/api.ts`)
  and applies the response to local room state (the WS event below also
  covers this for any other open tabs).
- `Sidebar.tsx` chat list item: `<AvatarCluster items={clusterFor(room)}
  .../>` is replaced with `<RoomLogo room={room} size={38} />` (no
  `editable`, matching your answer that the list itself stays read-only).
- `RoomView.tsx`'s existing WS handler gains a `case "room_logo_updated"`
  that patches local room state and calls `onActivity(roomId, {logo_url,
  logo_source})` — the same `patchRoom` path already used for `status` and
  `last_message`, so the sidebar entry for the *currently open* room
  updates live.

**Accepted limitation:** `listRooms()` only runs once, on `App` mount —
there is no polling and no app-wide WS subscription today. A room's
background-fetched logo only reaches the sidebar in real time while that
room is the one open in `RoomView` (whose own room-scoped WS connection
receives `room_logo_updated`). A different room's logo, fetched in the
background while you're looking elsewhere, only appears in the list after
a reload or by opening that room. This mirrors the already-accepted "no
live-refresh for other open tabs" gap called out for Instructions/Skills
in the Phase 2 polish backlog, and building an app-wide WS subscription
just for this cosmetic case would be disproportionate to what it fixes.

## Permissions & edge cases

- Upload permission tier matches skill upload: any room member, not
  owner-only.
- A room created before this feature shipped has `logo_blob_path=null`,
  `logo_source` backfilled to `"none"` by the migration — same as it never
  ran a lookup, so it falls back to the initials avatar exactly like a
  fresh room whose lookup came back empty.
- Brandfetch/blob-storage failures degrade to `logo_source="none"`, never
  to a paused room or a failed request — no user-visible error surface for
  a background job the user didn't directly trigger.
- Upload validation failures (wrong type, too large) return a 4xx with a
  message the file picker flow surfaces via the existing toast mechanism
  (`toastError`).

## Testing plan

- Backend: background job unit test with a mocked `httpx` transport
  (Brandfetch success, Brandfetch "no match", network failure — each
  lands on the correct `logo_source`); upload endpoint tests (valid file
  succeeds and sets `"custom"`, oversized/wrong-type rejected, non-member
  403); `AuditLog` rows for both `room_logo_fetched` and
  `room_logo_uploaded`; WS broadcast fires exactly once per fetch/upload.
- Frontend: no test runner in this repo (per established convention) —
  verify via `npx tsc --noEmit` plus a manual dev-server walkthrough:
  create a room and watch the logo fill in, upload a custom image and see
  it replace the fallback in both the header and the sidebar, confirm the
  read-only list item has no edit affordance.

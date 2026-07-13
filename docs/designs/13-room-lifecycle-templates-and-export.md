# Design 13 — Room Lifecycle, Templates & Export

**Status:** Proposed (feature)
**Value:** Directly serves the product's reason for existing — onboarding new
financial institutions faster and with an auditable paper trail. Templates make
each new customer room start pre-configured for a repeatable onboarding
playbook; lifecycle states + export turn the immutable transcript into a
deliverable (handover pack, MRM evidence) rather than a chat log someone has to
screenshot.
**Effort:** M (~1 sprint)

---

## Problem / opportunity

Rooms are minimal today: create with a customer name + free-text enrichment
([rooms.py:111](../../backend/app/api/rooms.py#L111)), then chat. There is no
notion of onboarding *stage*, no reusable starting configuration, no archival
state, and no way to get the transcript out. For a regulated onboarding process
that repeats per customer with similar steps and must produce evidence, that
leaves a lot of manual work on the table and the audit trail trapped in the app.

Three complementary capabilities:

1. **Templates** — a named bundle of enrichment + preloaded skills + initial
   seed prompt, so "start a standard bank onboarding" is one click.
2. **Lifecycle** — explicit room states beyond `active/paused` (e.g.
   `draft → onboarding → review → complete → archived`) to drive the lobby and
   reporting.
3. **Export** — render a room's transcript + metadata to a portable artifact
   (Markdown/PDF/JSON) as an onboarding deliverable and audit record.

## Goals

- Create a room from a reusable template that pre-populates enrichment and
  skills.
- Track and transition an onboarding lifecycle state, visible in the lobby.
- Export a room's full transcript + audit summary to Markdown/JSON (PDF
  optional), suitable as a handover/evidence document.

## Non-goals

- A workflow engine / task tracker (lifecycle is a small state machine, not
  BPMN).
- Editing historical messages (transcript stays immutable — see
  [Design 05](05-persistence-migrations-and-schema-integrity.md)).

## Design

### Templates

New `room_templates` table: `id`, `name`, `description`,
`enrichment_prompt`, `seed_message` (optional first human/system prompt),
`skills` (references to reusable skill definitions), `created_by`, timestamps.

- `POST /api/rooms` accepts an optional `template_id`; room creation copies the
  template's enrichment and clones its skills into the room (as room-scoped
  `AgentSkill` rows), optionally posting the seed message to kick off the loop.
- Admin CRUD for templates (gated by `require_admin`); a template can be
  authored from an existing room ("save this room's config as a template").
- Because templates carry skills/enrichment, they flow through the same
  isolation discipline as [Design 06](06-prompt-injection-and-untrusted-content.md).

### Lifecycle

Add `Room.lifecycle_state` (enum, via
[Design 05](05-persistence-migrations-and-schema-integrity.md)'s enum work),
independent of the `active/paused` loop status (which stays a runtime concern):

```
draft → onboarding → review → complete → archived
```

- `PATCH /api/rooms/{id}/lifecycle` transitions state (owner/admin), each
  writing an `audit_log` row and broadcasting a `room_lifecycle_changed` event.
- The lobby (`Sidebar`) groups/filters rooms by state; archived rooms are hidden
  by default (aligns with soft-delete). Reporting can count rooms per state.

### Export

`GET /api/rooms/{id}/export?format=md|json` (member/admin), returns:

- **Markdown:** a formatted transcript (participants, timestamps, cycle numbers,
  mention targets), the room's enrichment/skill configuration, the Drive link
  status, and a lifecycle/audit summary — a human-readable onboarding record.
- **JSON:** the structured equivalent for downstream systems.
- **PDF (optional/phase 2):** render the Markdown server-side.

Export is itself an audited action. Large rooms stream the response.

## Implementation sketch

- `db/models.py` + migration: `room_templates`, `Room.lifecycle_state`,
  reusable skill definitions (or reuse `AgentSkill` with a `template_id`).
- `api/rooms.py`: `template_id` on create; lifecycle PATCH; export endpoint.
- `api/admin.py` (or `api/templates.py`): template CRUD.
- `services/export.py`: transcript → Markdown/JSON (+ PDF later).
- `schemas.py`: template, lifecycle, export models; `room_lifecycle_changed`
  event.
- Frontend: template picker in the create-room dialog; lifecycle badge + filter
  in `Sidebar`; an "Export" action in `RoomView`.

## Testing

- Template create → room has the template's enrichment + cloned skills; seed
  message (if any) started the loop.
- Lifecycle transition writes audit + broadcasts the event; invalid transitions
  rejected.
- Export (md/json) contains every message in order with correct attribution and
  the config/audit summary; export writes an audit row; a room with >1000
  messages exports fully (not truncated by the REST list cap — reads directly,
  paginated internally).

## Rollout & risks

- Independent of most other designs; templates + export deliver value early.
- Lifecycle depends on [Design 05](05-persistence-migrations-and-schema-integrity.md)
  enums + soft-delete for clean modeling.
- **Risk:** export of a room with linked customer documents/citations may
  include sensitive content — gate by membership, audit every export, and
  consider a redaction pass before external sharing.

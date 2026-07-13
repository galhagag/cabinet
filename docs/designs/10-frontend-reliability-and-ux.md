# Design 10 — Frontend Reliability & UX

**Status:** Proposed

**Phase 1 progress:** C1 (missing `signIn`/`signOut` imports) shipped in
`fix/frontend-build-signin-signout-10`. Remaining: H7–H9, M10–M14, and the
frontend Lows — not yet started.

**Addresses:** C1 (missing `signIn`/`signOut` imports → build broken), H7 (REST
load replaces state, racing WS → dropped messages), H8 (reconnect never
resyncs), H9 (`initAuth().then` no `.catch` → blank page), M10 (own-message
detection breaks in Entra), M11 (draft cleared before send confirmed), M12
(infinite reconnect loop), M13 (invite lost across sign-in), M14 (Drive
`error` shown as linked), and frontend Lows (localStorage tokens, modal a11y,
auto-scroll, poll-timer ref, hardcoded copy, unvalidated casts, WS path prefix).
**Effort:** M (~1 sprint)

---

## Problem

The frontend has one build-breaking bug and a cluster of realtime/auth/UX
defects that mostly bite in the production Entra + Web PubSub configuration —
i.e. they're invisible in the mocked dev loop and surface only on Azure.

Most severe:

- **C1 — build broken.** `App.tsx` calls `signIn()`/`signOut()` (lines 82, 94)
  but never imports them; `tsc --noEmit` fails `TS2304` (confirmed), so
  `npm run build` fails and, under `vite dev`, the Microsoft sign-in button
  throws `ReferenceError`. Entra sign-in is completely dead.
- **H7 — dropped messages on load.** `RoomView` initial fetch does
  `setMessages(msgs)` (replace) while the WS connects concurrently; an event
  arriving between the server snapshot and the REST apply is wiped
  ([RoomView.tsx:107](../../frontend/src/components/RoomView.tsx#L107)).
- **H8 — no resync on reconnect.** `RoomSocket` reconnects but exposes no
  reconnected hook and RoomView never refetches
  ([ws.ts:59](../../frontend/src/ws.ts#L59)); messages during a sleep/blip are
  permanently missing, with no disconnected indicator.
- **H9 — blank page on auth error.** `initAuth().then(render)` has no `.catch`
  ([main.tsx:14](../../frontend/src/main.tsx#L14)); any MSAL failure renders
  nothing.

## Goals

- The app builds and typechecks clean; Entra sign-in works.
- The message thread is eventually-consistent under connect, reconnect, and
  concurrent WS/REST — no dropped or duplicated messages, ever.
- Auth and network failures produce visible, actionable UI states, never blank
  pages or silent loss.
- Own-message identity, drafts, and invites survive the Entra flow.

## Design

### 1. Fix the build (C1)

Import `signIn`, `signOut` from `./auth` in `App.tsx` (verify the exports exist
in [auth.ts](../../frontend/src/auth.ts); add them if missing). Add `tsc
--noEmit` to CI ([Design 09](09-ci-quality-gates-and-supply-chain.md)) so this
class of error can never merge again.

### 2. One consistent message store (H7, H8)

Make realtime the source of truth with REST as backfill, via a single reducer:

- A `useMessages(roomId)` hook holding a `Map<id, Message>` keyed by message id,
  sorted by `(seq, id)`. Both the REST fetch and every WS `message_created` go
  through `merge(messages)` (idempotent upsert) — never `replace`. This fixes
  H7 directly.
- Add an `onReconnected` callback to `RoomSocket` (fired from `onopen` after the
  first successful (re)connect). On reconnect, RoomView calls `listMessages`
  again and merges — closing the gap for anything missed while disconnected
  (H8). With the server-side `seq` from
  [Design 05](05-persistence-migrations-and-schema-integrity.md), this can later
  become an incremental "give me everything after seq N" resync
  ([Design 04](04-realtime-fanout-and-webpubsub.md)'s `desync` marker triggers
  the same path).
- A visible connection indicator: `connecting | live | reconnecting | offline`,
  driven by socket state, so the user knows when the thread might be stale.

### 3. Robust auth bootstrap (H9, M13)

- Wrap `initAuth()` in try/catch in `main.tsx`; on failure render an error
  screen ("Sign-in unavailable — check configuration / retry"), never nothing.
- Preserve the invite across sign-in: before `loginRedirect`, stash
  `?token=` (sessionStorage or the MSAL `state`), and on return replay it into
  the join flow so an invited stakeholder actually joins the room (M13). Set
  `redirectUri` back to the full URL including the invite, or reconstruct it
  post-login.

### 4. Identity, drafts, uploads (M10, M11)

- **M10:** own-message detection must use the *authenticated* identity. In Entra
  mode read the signed-in account (MSAL `getActiveAccount()` username / verified
  claim), not the localStorage dev email, and compare against `sender_name` the
  backend sets from the token. Better: have the backend stamp a stable
  `sender_id` (the `oid` from [Design 03](03-authorization-and-tenancy-hardening.md))
  and compare on that rather than display name.
- **M11:** keep the draft until the send resolves. Optimistic-send: render the
  message immediately in a `pending` state, clear the composer, and on failure
  mark it `failed` with a retry button (and restore into the composer on retry).
  Coordinates with the `202`/idempotency contract from
  [Design 02](02-orchestrator-resilience-and-durable-loop.md) Stage 3.

### 5. Reconnect backoff & error surfaces (M12, M14)

- **M12:** exponential backoff with jitter and a cap; reset the attempt counter
  only after the connection has been *stable* for a threshold (e.g. 5s), not
  merely on `onopen` — so an accept-then-immediately-close server (rejected
  token, deleted room) backs off instead of hot-looping. After N failures, drop
  to `offline` with a manual "reconnect" affordance.
- **M14:** `DrivePanel` must handle `status === "error"` explicitly (error
  message + "Try again"), not fall through to the "Linked" branch.

### 6. Polish (Lows)

- MSAL cache: prefer `sessionStorage` (or in-memory) over `localStorage`
  ([auth.ts:44](../../frontend/src/auth.ts#L44)) to shrink token exposure.
- Modals (`Sidebar`, `InviteDialog`, `SkillUploadDialog`): add `role="dialog"`,
  `aria-modal`, focus trap, Escape-to-close, and return focus to the trigger.
- Auto-scroll only when the user is already at the bottom; otherwise show a
  "jump to latest" pill ([ChatThread.tsx:54](../../frontend/src/components/ChatThread.tsx#L54)).
  Memoize message rows.
- `LoopBudgetBanner` copy: use the dynamic `cycleLimit` prop, not hardcoded "6"
  ([LoopBudgetBanner.tsx:33](../../frontend/src/components/LoopBudgetBanner.tsx#L33)).
- API client: validate responses (zod or a light runtime check) instead of
  `as T` ([api.ts:70](../../frontend/src/api.ts#L70)); handle empty/204 bodies.
- `wsUrl`: preserve any API path prefix so a path-based deployment doesn't break
  the socket ([ws.ts:7](../../frontend/src/ws.ts#L7)).
- **Markdown:** agents emit markdown that currently renders as raw `**text**`.
  Render with `react-markdown` + `rehype-sanitize` (never `dangerouslySetInnerHTML`)
  so formatting works without opening an XSS sink — pairs with
  [Design 06](06-prompt-injection-and-untrusted-content.md)'s output-escaping note.

## Implementation sketch

- `App.tsx`: imports (C1); invite-preservation.
- `main.tsx`: try/catch bootstrap + error screen.
- `ws.ts`: `onReconnected`; stable-connection backoff reset; jittered cap; path
  prefix.
- new `hooks/useMessages.ts`: id-keyed merge store.
- `RoomView.tsx`: use the store; refetch-on-reconnect; connection indicator.
- `ChatThread.tsx`: identity via authenticated principal; smart auto-scroll;
  `memo`; markdown rendering.
- `Composer.tsx`: optimistic/failed/retry.
- `DrivePanel.tsx`: `error` branch; null the poll-timer ref.
- `auth.ts`: cache location; ensure `signIn`/`signOut` exports.
- modals: a shared `<Modal>` primitive with a11y baked in.

## Testing

- Introduce a frontend test setup (Vitest + Testing Library — none exists today,
  per [Design 09](09-ci-quality-gates-and-supply-chain.md)).
- `useMessages`: merging REST + WS with overlapping ids yields no dupes and
  correct order; a WS event arriving before the REST apply is preserved (H7).
- `RoomSocket`: simulate accept-then-close → backoff grows and caps (M12);
  `onReconnected` fires and triggers a refetch (H8).
- `main` bootstrap: a rejected `initAuth` renders the error screen, not blank
  (H9).
- Component: Entra-mode own-message alignment (M10); Drive `error` state (M14);
  optimistic send failure shows retry (M11).

## Rollout & risks

- C1 is a one-line unblock — ship immediately.
- The message-store refactor (H7/H8) is the largest piece; it's isolated behind
  the `useMessages` hook so it can land without touching unrelated components.
- Markdown rendering changes visible output; gate behind `rehype-sanitize` and
  review the agent output styling.

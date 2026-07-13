# Chat UI Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render chat message content as formatted Markdown, and replace the always-visible "Loop budget" meter with a lean alert that only appears when agents are paused awaiting a human.

**Architecture:** Two independent frontend-only changes to the Cabinet room UI (`frontend/src/components/`). No backend changes in either task. Both tasks touch `frontend/src/styles.css` in non-overlapping line ranges — run them sequentially to avoid merge conflicts on that file.

**Tech Stack:** React 18 + TypeScript + Vite (existing). New dependencies: `react-markdown`, `remark-gfm`.

## Global Constraints

- Do not add a syntax-highlighting library (e.g. `rehype-highlight`, `react-syntax-highlighter`) — keep the dependency footprint small; code blocks get plain monospace styling only.
- Do not enable raw HTML rendering in `react-markdown` (no `rehype-raw` plugin) — message content includes LLM output and must not execute embedded HTML/script tags.
- This app has a single light theme with no dark-mode CSS variables — use only the existing `--text`, `--text-muted`, `--accent`, `--border`, `--border-strong`, `--bg-input`, `--radius` custom properties from `styles.css:3-25`. Do not introduce new theme tokens.
- No backend changes for either task.
- Do not introduce a new frontend testing framework — this repo has none (`frontend/package.json` has no test runner, no `*.test.*` files exist). Verify manually via `npm run dev` instead of writing automated tests.
- Links inside rendered markdown must open in a new tab with `rel="noopener noreferrer"`.
- `room.cycles_used` / `room.cycle_limit` stay in the `RoomOut` type and WS-event handling in `RoomView.tsx` — do not remove them from state, only stop rendering them.
- Reference spec: `docs/superpowers/specs/2026-07-13-chat-ui-polish-design.md`

---

### Task 1: Markdown rendering for chat messages

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/src/components/ChatThread.tsx:88`
- Modify: `frontend/src/styles.css` (append after line 781)

**Interfaces:**
- Consumes: `msg.content: string` (already exists on `MessageOut`, `frontend/src/types.ts`).
- Produces: nothing consumed by Task 2 — fully independent.

- [ ] **Step 1: Add markdown dependencies to `frontend/package.json`**

Edit the `dependencies` block so it reads:

```json
  "dependencies": {
    "@azure/msal-browser": "^3.27.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-markdown": "^9.0.1",
    "remark-gfm": "^4.0.0"
  },
```

- [ ] **Step 2: Install and verify**

Run: `cd frontend && npm install`
Expected: installs `react-markdown` and `remark-gfm` with no errors, `package-lock.json` updated.

- [ ] **Step 3: Render message content as Markdown in `ChatThread.tsx`**

At the top of `frontend/src/components/ChatThread.tsx`, add imports (after the existing `import { Avatar } from "./Avatar";` on line 4):

```tsx
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";
```

Below the existing helper functions (after `TokenUsage`, before `export default function ChatThread`), add:

```tsx
const markdownComponents: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
  table: ({ children }) => (
    <div className="md-table-wrap">
      <table>{children}</table>
    </div>
  ),
};
```

Replace line 88:

```tsx
              <div className="msg-content">{msg.content}</div>
```

with:

```tsx
              <div className="msg-content">
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                  {msg.content}
                </ReactMarkdown>
              </div>
```

Do **not** change the "is thinking…" block (around line 104-108) — that renders static JSX, not `msg.content`, and must stay as plain text.

- [ ] **Step 4: Verify TypeScript compiles**

Run: `cd frontend && npm run build`
Expected: build succeeds with no TypeScript errors (this is the closest thing to a test cycle available in this repo — there is no unit test runner).

- [ ] **Step 5: Add Markdown styling to `frontend/src/styles.css`**

Insert immediately after the existing `.msg-content { white-space: pre-wrap; word-break: break-word; }` block (`styles.css:778-781`):

```css
.msg-content p {
  margin: 0.3em 0;
}

.msg-content p:first-child {
  margin-top: 0;
}

.msg-content p:last-child {
  margin-bottom: 0;
}

.msg-content ul,
.msg-content ol {
  margin: 0.3em 0;
  padding-left: 1.4em;
}

.msg-content li {
  margin: 0.15em 0;
}

.msg-content h1,
.msg-content h2,
.msg-content h3,
.msg-content h4,
.msg-content h5,
.msg-content h6 {
  margin: 0.5em 0 0.3em;
  line-height: 1.3;
  font-weight: 700;
}

.msg-content h1 {
  font-size: 1.25rem;
}

.msg-content h2 {
  font-size: 1.15rem;
}

.msg-content h3 {
  font-size: 1.05rem;
}

.msg-content h4,
.msg-content h5,
.msg-content h6 {
  font-size: 1rem;
}

.msg-content code {
  background: var(--bg-input);
  border-radius: 4px;
  padding: 0.1em 0.35em;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  font-size: 0.85em;
}

.msg-content pre {
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.6em 0.8em;
  overflow-x: auto;
  margin: 0.4em 0;
}

.msg-content pre code {
  background: none;
  padding: 0;
  font-size: 0.85em;
}

.msg-content blockquote {
  border-left: 3px solid var(--border-strong);
  color: var(--text-muted);
  margin: 0.4em 0;
  padding: 0.1em 0 0.1em 0.8em;
}

.msg-content .md-table-wrap {
  overflow-x: auto;
  margin: 0.4em 0;
}

.msg-content table {
  border-collapse: collapse;
  font-size: 0.9em;
}

.msg-content th,
.msg-content td {
  border: 1px solid var(--border);
  padding: 0.3em 0.6em;
  text-align: left;
}

.msg-content a {
  color: var(--accent);
}
```

- [ ] **Step 6: Manual verification**

Run: `cd frontend && npm run dev`, open the app, enter a room, and post a message (or use the mock agent reply path already in the backend) containing:

```
**bold**, *italic*, `inline code`

# Header

- item one
- item two

| A | B |
|---|---|
| 1 | 2 |

```js
console.log("hi")
```

<script>alert(1)</script>
```

Expected:
- Bold/italic/inline code render styled, not literal `**`/`*`/`` ` ``.
- Header, bullet list, and table render formatted.
- The fenced code block renders in a monospace box.
- The literal `<script>` tag renders as visible text (e.g. `&lt;script&gt;alert(1)&lt;/script&gt;`), and does **not** execute — confirms no raw HTML/XSS risk was introduced.

- [ ] **Step 7: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/components/ChatThread.tsx frontend/src/styles.css
git commit -m "Render chat message content as Markdown"
```

---

### Task 2: Replace Loop Budget meter with a lean paused alert

**Files:**
- Delete: `frontend/src/components/LoopBudgetBanner.tsx`
- Create: `frontend/src/components/PausedBanner.tsx`
- Modify: `frontend/src/components/RoomView.tsx:8` (import), `RoomView.tsx:259-267` (render block)
- Modify: `frontend/src/styles.css:597-643` (remove)

**Interfaces:**
- Produces: `PausedBanner` component with props `{ status: string; onResume: () => void; resuming: boolean }`, renders `null` unless `status === "paused_awaiting_human"`.
- Consumes from `RoomView.tsx`: existing `room.status`, `resume` callback (`RoomView.tsx:186-208`), `resuming` state (`RoomView.tsx:34`) — no signature changes to any of these.

- [ ] **Step 1: Create `frontend/src/components/PausedBanner.tsx`**

```tsx
export default function PausedBanner({
  status,
  onResume,
  resuming,
}: {
  status: string;
  onResume: () => void;
  resuming: boolean;
}) {
  if (status !== "paused_awaiting_human") return null;

  return (
    <div className="paused-alert">
      <span className="paused-text">
        Agents paused — 6-turn autonomous budget reached. Post a message or resume.
      </span>
      <button className="btn btn-resume" onClick={onResume} disabled={resuming}>
        {resuming ? "Resuming…" : "Resume"}
      </button>
    </div>
  );
}
```

- [ ] **Step 2: Delete `frontend/src/components/LoopBudgetBanner.tsx`**

```bash
git rm frontend/src/components/LoopBudgetBanner.tsx
```

- [ ] **Step 3: Update `frontend/src/components/RoomView.tsx`**

Replace the import on line 8:

```tsx
import LoopBudgetBanner from "./LoopBudgetBanner";
```

with:

```tsx
import PausedBanner from "./PausedBanner";
```

Replace the render block on lines 259-267:

```tsx
      {room && (
        <LoopBudgetBanner
          status={room.status}
          cyclesUsed={room.cycles_used}
          cycleLimit={room.cycle_limit}
          onResume={resume}
          resuming={resuming}
        />
      )}
```

with:

```tsx
      {room && <PausedBanner status={room.status} onResume={resume} resuming={resuming} />}
```

Leave every other reference to `room.cycles_used` / `room.cycle_limit` in `RoomView.tsx` (e.g. lines 79-80, 141-142, 173-174, 196-197) untouched — those keep local state in sync with the backend and are out of scope.

- [ ] **Step 4: Verify TypeScript compiles**

Run: `cd frontend && npm run build`
Expected: build succeeds with no TypeScript errors, and no remaining references to `LoopBudgetBanner` (confirm with `grep -rn "LoopBudgetBanner" frontend/src` — expect no output).

- [ ] **Step 5: Clean up `frontend/src/styles.css`**

Remove the entire block from the `/* ---- loop budget banner ---- */` comment through the end of `.loop-meter-full` (currently `styles.css:597-643`):

```css
/* ---- loop budget banner ---------------------------------------------------- */
.loop-banner {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.6rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.55rem;
}

.loop-banner-paused {
  border-color: var(--warn);
  box-shadow: 0 0 0 1px rgba(210, 153, 34, 0.35);
}

.loop-meter {
  display: flex;
  align-items: center;
  gap: 0.8rem;
}

.loop-meter-label {
  font-size: 0.82rem;
  color: var(--text-muted);
  white-space: nowrap;
}

.loop-meter-track {
  flex: 1;
  height: 8px;
  background: var(--bg-input);
  border-radius: 999px;
  overflow: hidden;
}

.loop-meter-fill {
  height: 100%;
  background: var(--accent);
  border-radius: 999px;
  transition: width 0.3s ease;
}

.loop-meter-full {
  background: var(--warn);
}
```

Replace the comment line directly above `.paused-alert` (immediately following the deleted block) with:

```css
/* ---- paused banner ---------------------------------------------------- */
```

Leave `.paused-alert` and `.paused-text` (currently `styles.css:644-658`) exactly as they are — they become the root styling for `PausedBanner` directly, and `.room-view`'s existing `gap: 0.8rem` (`styles.css:540`) already provides spacing above/below it.

- [ ] **Step 6: Manual verification**

Run: `cd frontend && npm run dev`, open a room:
- While the room is active (not paused), confirm no banner/meter of any kind appears between the header and the chat thread.
- Trigger the pause condition (send messages until the backend returns `room_status: "paused_awaiting_human"`, or use the existing `/api/rooms/{id}/resume` flow in reverse via the mock agent if available) and confirm the lean alert ("Agents paused — 6-turn autonomous budget reached…" + Resume button) appears with no numeric meter.
- Click **Resume** and confirm the room resumes (banner disappears, agents respond) — this exercises the unchanged `resume()` handler in `RoomView.tsx:186-208`.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/PausedBanner.tsx frontend/src/components/RoomView.tsx frontend/src/styles.css
git commit -m "Replace Loop Budget meter with a lean paused-only alert"
```

(`LoopBudgetBanner.tsx`'s deletion was already staged by `git rm` in Step 2.)

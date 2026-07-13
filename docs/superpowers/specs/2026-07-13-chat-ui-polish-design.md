# Chat UI Polish — Design Spec

Date: 2026-07-13

## Summary

Two independent, small frontend changes to the Cabinet chat room UI:

1. Render chat message content as Markdown instead of raw text.
2. Remove the numeric "Loop budget" meter from the UI, keeping a lean paused/Resume alert.

This is Spec A of a two-spec split; Spec B covers renaming "Upload skill" to an "Agents Config" panel with MCP server management and live tool-calling integration, and will be brainstormed separately given its larger scope.

## Part 1 — Markdown rendering

### Problem

`ChatThread.tsx:88` renders `msg.content` as a plain escaped string:

```tsx
<div className="msg-content">{msg.content}</div>
```

No markdown library exists in the frontend. LLM-authored agent replies (and any human replies) that use `**bold**`, lists, tables, code fences, etc. show up as literal raw characters.

### Approach

- Add `react-markdown` and `remark-gfm` to `frontend/package.json`.
- In `ChatThread.tsx`, replace the raw text node with:
  ```tsx
  <div className="msg-content">
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
      {msg.content}
    </ReactMarkdown>
  </div>
  ```
  `markdownComponents` overrides `p` (and similar block elements) to avoid extra top/bottom margins that would break the compact chat-bubble spacing.
- Supported syntax via `remark-gfm`: bold/italic, headers, ordered/unordered lists, tables, links, blockquotes, strikethrough, fenced code blocks. Code blocks get plain monospace + background styling — no syntax highlighter is added (keeps the dependency footprint small; can be revisited later if needed).
- **Security**: `react-markdown` does not render raw HTML unless the `rehype-raw` plugin is added. We do **not** add it. This means any literal `<script>` or HTML tags inside message content (human or agent authored) render as inert escaped text, not live markup — preserving the current safety property of the raw-text renderer while adding formatting.
- Links render with `target="_blank" rel="noopener noreferrer"` via a custom `a` component override.

### Styling

New CSS rules scoped under `.msg-content` in `styles.css`, using existing theme variables (`--text`, `--text-muted`, `--accent`, `--border`, `--bg-input`, `--radius`) — this app has a single light theme today (no dark-mode tokens), so no new theme variables are introduced:

- Headers: reduced font-size scale appropriate for a chat bubble (not full document-sized headers).
- Lists: standard indent, tight `margin` to match bubble density.
- Code (inline): `--bg-input` background, monospace font stack already used elsewhere (`styles.css:933`).
- Code (fenced blocks): same background, `overflow-x: auto`, padding, rounded via `--radius`.
- Tables: `overflow-x: auto` wrapper so wide tables don't overflow the bubble; border using `--border`.
- Blockquotes: left border in `--border-strong`, muted text color.

### Out of scope

- Syntax highlighting for code blocks.
- Rendering raw HTML embedded in messages.
- Backend changes — `msg.content` is already plain text/markdown source; no schema changes needed.

## Part 2 — Remove Loop Budget meter

### Problem

`LoopBudgetBanner.tsx` always renders a "Loop budget {cyclesUsed}/{cycleLimit}" progress meter above the chat thread, even during normal active conversation. This is being removed per user request; the underlying pause/resume mechanism (backend enforced cycle budget) is unchanged — only its numeric visualization goes away.

### Approach

- Delete `frontend/src/components/LoopBudgetBanner.tsx`.
- Add `frontend/src/components/PausedBanner.tsx`:
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
- In `RoomView.tsx`:
  - Replace the `LoopBudgetBanner` import with `PausedBanner`.
  - Replace the render block (currently lines 259-267) with:
    ```tsx
    {room && <PausedBanner status={room.status} onResume={resume} resuming={resuming} />}
    ```
  - `room.cycles_used` / `room.cycle_limit` remain in the `RoomOut` type and WS-event handling (unused for display now, but no schema/type changes needed — they're harmless unused-for-render fields still driven by existing WS events).
- In `styles.css`:
  - Remove `.loop-banner`, `.loop-banner-paused`, `.loop-meter`, `.loop-meter-label`, `.loop-meter-track`, `.loop-meter-fill`, `.loop-meter-full` (lines 598-643).
  - Keep `.paused-alert` / `.paused-text` (currently lines 644-658), used directly as the root class of `PausedBanner`.

### Out of scope

- No backend changes. The 6-cycle budget enforcement in `backend/app/agents/orchestrator.py` is untouched — this is a display-only change.
- Not reusing the existing unmerged `feature/hide-loop-budget-and-edit-message` branch (per user decision) — implementing fresh as part of this spec instead.

## Testing

- Frontend has no existing component test setup found for `ChatThread`/`LoopBudgetBanner` (verify during implementation; add lightweight tests only if a test harness already exists — don't introduce a new testing framework for this).
- Manual verification: run the frontend dev server, open a room, confirm:
  - A message containing `**bold**`, a list, a table, and a fenced code block renders formatted, not literal.
  - A message containing `<script>alert(1)</script>` renders as literal text, not executed.
  - The Loop Budget meter no longer appears during active conversation.
  - When a room is paused (`status === "paused_awaiting_human"`), the lean paused alert + Resume button appears and resume still works end-to-end.

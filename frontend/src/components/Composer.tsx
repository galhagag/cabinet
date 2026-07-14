import { useRef, useState, type KeyboardEvent } from "react";

const MENTIONS = [
  { handle: "@DataExpert", label: "Data Expert — data science & engineering" },
  { handle: "@FCE", label: "FCE — financial crime / AML compliance" },
];

export default function Composer({
  onSend,
  sending,
  disabled,
  disabledHint,
}: {
  onSend: (content: string) => Promise<boolean>;
  sending: boolean;
  disabled?: boolean;
  disabledHint?: string;
}) {
  const [value, setValue] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerIndex, setPickerIndex] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const blocked = sending || !!disabled;

  const send = async () => {
    const content = value.trim();
    if (!content || blocked) return;
    const sent = await onSend(content);
    if (!sent) return;
    setValue("");
    setPickerOpen(false);
  };

  const insertMention = (handle: string) => {
    const el = textareaRef.current;
    if (!el) return;
    const pos = el.selectionStart ?? value.length;
    // Replace the "@" (and any partial word) that opened the picker.
    const before = value.slice(0, pos).replace(/@\w*$/, "");
    const after = value.slice(pos);
    const next = `${before}${handle} ${after}`;
    setValue(next);
    setPickerOpen(false);
    requestAnimationFrame(() => {
      el.focus();
      const caret = before.length + handle.length + 1;
      el.setSelectionRange(caret, caret);
    });
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (pickerOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setPickerIndex((i) => (i + 1) % MENTIONS.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setPickerIndex((i) => (i - 1 + MENTIONS.length) % MENTIONS.length);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        const m = MENTIONS[pickerIndex];
        if (m) insertMention(m.handle);
        return;
      }
      if (e.key === "Escape") {
        setPickerOpen(false);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  return (
    <div className="composer">
      {pickerOpen && (
        <div className="mention-picker">
          {MENTIONS.map((m, i) => (
            <button
              key={m.handle}
              className={`mention-option ${i === pickerIndex ? "mention-selected" : ""}`}
              onMouseEnter={() => setPickerIndex(i)}
              onMouseDown={(e) => {
                e.preventDefault();
                insertMention(m.handle);
              }}
            >
              <span className="mention-handle">{m.handle}</span>
              <span className="mention-label">{m.label}</span>
            </button>
          ))}
        </div>
      )}
      <div className="composer-row">
        <textarea
          ref={textareaRef}
          value={value}
          rows={3}
          placeholder={
            blocked && disabledHint
              ? disabledHint
              : "Message the Cabinet… type @ to mention an expert. Enter to send, Shift+Enter for newline."
          }
          disabled={blocked}
          onChange={(e) => {
            const next = e.target.value;
            setValue(next);
            const caret = e.target.selectionStart ?? next.length;
            const beforeCaret = next.slice(0, caret);
            const open = /(^|\s)@\w*$/.test(beforeCaret);
            setPickerOpen(open);
            if (open) setPickerIndex(0);
          }}
          onKeyDown={handleKeyDown}
        />
        <button className="btn btn-primary composer-send" onClick={send} disabled={blocked || !value.trim()}>
          {sending ? "Sending…" : "Send"}
        </button>
      </div>
      {sending && <div className="composer-hint">Waiting for the Cabinet to respond…</div>}
      {!sending && disabled && disabledHint && <div className="composer-hint">{disabledHint}</div>}
    </div>
  );
}

// Minimal module-level toast bus so any component can surface errors visibly.

export interface Toast {
  id: number;
  kind: "error" | "info";
  text: string;
}

type Listener = (toasts: Toast[]) => void;

let toasts: Toast[] = [];
let nextId = 1;
const listeners = new Set<Listener>();

function publish(): void {
  for (const l of listeners) l(toasts);
}

export function subscribeToasts(listener: Listener): () => void {
  listeners.add(listener);
  listener(toasts);
  return () => {
    listeners.delete(listener);
  };
}

export function pushToast(kind: Toast["kind"], text: string, ttlMs = 6000): void {
  const id = nextId++;
  toasts = [...toasts, { id, kind, text }];
  publish();
  window.setTimeout(() => dismissToast(id), ttlMs);
}

export function dismissToast(id: number): void {
  if (!toasts.some((t) => t.id === id)) return;
  toasts = toasts.filter((t) => t.id !== id);
  publish();
}

export function toastError(err: unknown, prefix?: string): void {
  const msg = err instanceof Error ? err.message : String(err);
  pushToast("error", prefix ? `${prefix}: ${msg}` : msg);
}

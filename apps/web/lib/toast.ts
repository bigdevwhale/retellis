// I35: a tiny toast store. Kept framework-light (a module-level emitter) so
// any code — inside or outside React, in a zustand action or a click handler —
// can surface a transient success/error message without prop-drilling. The
// <Toaster/> component (mounted once at the app root) subscribes and renders
// the stack.
//
// i18n: callers pass ALREADY-LOCALIZED strings (they have `t` at the call site);
// the store itself is i18n-agnostic and never imports the lang context. This
// keeps it usable from non-React modules (the store, api-client) that can't
// call hooks.
//
// Auto-dismiss: a toast without an action dismisses after 5s; one WITH an
// action (e.g. Undo) lasts 8s so the user has time to click. Pass duration: 0
// for a sticky toast (caller must dismiss).

export type ToastKind = 'success' | 'error' | 'info';

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
  action?: ToastAction;
  duration: number; // ms; 0 = sticky (no auto-dismiss)
}

type Listener = (toasts: Toast[]) => void;

let nextId = 1;
let toasts: Toast[] = [];
const listeners = new Set<Listener>();

function emit(): void {
  for (const l of listeners) l(toasts);
}

/** Subscribe to the toast stack. Returns an unsubscribe fn. */
export function subscribe(l: Listener): () => void {
  listeners.add(l);
  // Emit the current snapshot immediately so a freshly-mounted <Toaster/>
  // doesn't miss a toast pushed during the same tick.
  l(toasts);
  return () => {
    listeners.delete(l);
  };
}

export function dismiss(id: number): void {
  const next = toasts.filter((tt) => tt.id !== id);
  if (next.length === toasts.length) return; // no-op; avoid spurious emits
  toasts = next;
  emit();
}

interface PushOptions {
  action?: ToastAction;
  duration?: number;
}

function push(kind: ToastKind, message: string, opts?: PushOptions): number {
  const id = nextId++;
  const duration = opts?.duration ?? (opts?.action ? 8000 : 5000);
  toasts = [...toasts, { id, kind, message, action: opts?.action, duration }];
  emit();
  if (duration > 0) {
    setTimeout(() => dismiss(id), duration);
  }
  return id;
}

export const toast = {
  success: (message: string, opts?: { duration?: number }) => push('success', message, opts),
  error: (message: string, opts?: { duration?: number }) => push('error', message, opts),
  info: (message: string, opts?: { action?: ToastAction; duration?: number }) =>
    push('info', message, opts),
  dismiss,
};

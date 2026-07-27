'use client';

// I35: the single mount point for the toast stack. Subscribes to the
// framework-light toast store (lib/toast.ts) and renders the live stack.
// Mounted once in app/layout.tsx so any module can `toast.error(...)` without
// wiring. aria-live="polite" so screen readers announce non-error toasts
// without interrupting; error toasts use role="alert" for immediate notice.

import { type Toast, dismiss, subscribe } from '@/lib/toast';
import { useEffect, useState } from 'react';

export function Toaster() {
  const [items, setItems] = useState<Toast[]>([]);

  useEffect(() => subscribe(setItems), []);

  if (items.length === 0) return null;

  return (
    <section className="toaster" aria-label="Notifications">
      {items.map((tt) => (
        <div
          key={tt.id}
          className={`toast toast-${tt.kind}`}
          role={tt.kind === 'error' ? 'alert' : 'status'}
        >
          <span className="toast-msg">{tt.message}</span>
          {tt.action && (
            <button
              type="button"
              className="toast-action"
              onClick={() => {
                tt.action?.onClick();
                dismiss(tt.id);
              }}
            >
              {tt.action.label}
            </button>
          )}
          <button
            type="button"
            className="toast-close"
            aria-label="Dismiss notification"
            onClick={() => dismiss(tt.id)}
          >
            <svg
              viewBox="0 0 24 24"
              width="14"
              height="14"
              aria-hidden="true"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
      ))}
    </section>
  );
}

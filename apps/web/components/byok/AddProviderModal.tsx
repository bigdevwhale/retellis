'use client';

// Modal that hosts the per-provider key form. Used by the personal Key vault
// (Settings) and the family Family key tab, and as the "Add another key"
// affordance from the onboarding summary card. The modal is dumb: the parent
// owns the encryption + the API call. The modal just shows the form, fires
// onSubmit when the user clicks Add, and closes on cancel/success.
//
// Mirrors the NewChatPicker pattern: a full-viewport .picker-overlay with a
// centred .picker box, Escape closes, click on the backdrop closes.

import { type ReactNode, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { ProviderKeyForm, type ProviderKeyFormValues } from './ProviderKeyForm';

type Props = {
  open: boolean;
  onClose: () => void;
  onSubmit: (values: ProviderKeyFormValues) => void | Promise<void>;
  // Title rendered at the top of the modal. i18n key is the caller's job.
  title: string;
  submitLabel?: string;
  initial?: Partial<ProviderKeyFormValues> & { kind?: ProviderKeyFormValues['kind'] };
  busy?: boolean;
  // Slot for extra copy the caller wants above the form (e.g. "This unlocks
  // the family key surface for everyone in the family"). Rendered between
  // the title and the form.
  intro?: ReactNode;
};

export function AddProviderModal({
  open,
  onClose,
  onSubmit,
  title,
  submitLabel,
  initial,
  busy,
  intro,
}: Props) {
  // Escape closes. The picker overlay only renders when `open`, so attaching
  // the listener on the document is fine — it's a no-op otherwise.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;
  // Portal to document.body so the overlay escapes any ancestor stacking
  // context — notably the family page's `.fam-wrap > *` entrance animation
  // (``fam-rise`` animates transform/opacity, which briefly establishes a
  // stacking context on each direct child). The modal used to be a DOM
  // descendant of the key-tab card, so during that animation a later sibling
  // (the "Rename family" card) painted over the overlay. Portaling puts the
  // ``position: fixed; z-index: 60`` overlay at the root stacking context,
  // above every page element regardless of ancestor animations.
  if (typeof document === 'undefined') return null;

  return createPortal(
    // The modal is a real accessible dialog — a `div role="dialog"` is the
    // standard pattern for React modals (the native `<dialog>` element
    // would conflict with the in-app focus management here). Biome's
    // `useSemanticElements` rule suggests swapping to `<dialog>`, but that
    // changes the rendering model (top-layer, ::backdrop, form-method
    // behavior) and would break the picker overlay CSS. Keep the explicit
    // `role="dialog"` and silence the suggestion with a narrow disable.
    <div
      className="picker-overlay"
      // biome-ignore lint/a11y/useSemanticElements: see comment above
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onMouseDown={(e) => {
        // Backdrop click closes. ``mousedown`` (not ``click``) so a stray
        // press inside the picker doesn't bubble here after a form commit.
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="picker byok-modal" onMouseDown={(e) => e.stopPropagation()}>
        <h2 className="byok-modal-title">{title}</h2>
        {intro && <div className="byok-modal-intro">{intro}</div>}
        <ProviderKeyForm
          initial={initial}
          onSubmit={onSubmit}
          onCancel={onClose}
          submitLabel={submitLabel}
          busy={busy}
        />
      </div>
    </div>,
    document.body,
  );
}

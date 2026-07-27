'use client';

// Picks between the real authenticated screen and the guest informational
// showcase for an OD feature route. Used by the feature `app/*/page.tsx`
// files so a signed-out visitor browsing the landing can open each page from
// the header nav and see an OD-style showcase (sample content + a sign-in CTA)
// instead of a 401 or a redirect to /login.
//
// `hasSession` is read server-side from the (HttpOnly) cookie presence in
// app/layout.tsx and passed down — so the decision is flash-free:
//   - principal resolved (signed in)            → real screen
//   - cookie present but /me still in flight     → real screen, optimistically
//     (matches the old AuthGate "render children optimistically" behaviour;
//      a stale cookie is caught by AuthGate's redirect)
//   - no cookie (guest)                          → showcase, immediately
//
// /practices does not use this wrapper — the practices screen is fully
// client-side (no API, no key, no per-user data), so it is the same honest,
// working tool for a guest as for a signed-in user.

import type { FeatureKey } from '@/components/GuestShowcase';
import { GuestShowcase } from '@/components/GuestShowcase';
import { useAuthCtx } from '@/lib/auth';

export function GuestFeature({
  feature,
  hasSession,
  real,
}: {
  feature: FeatureKey;
  hasSession: boolean;
  real: React.ReactNode;
}) {
  const { principal } = useAuthCtx();
  if (principal || hasSession) return <>{real}</>;
  return <GuestShowcase feature={feature} />;
}

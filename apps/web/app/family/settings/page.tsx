'use client';

// Deep-link backstop. The actual UI for the family settings now lives
// at /family?tab=settings (a sub-tab inside the top-level /family tab
// strip). The /family/settings route is kept so any bookmarked / shared
// link — ChatScreen lockout banner, the prior /family/vault page, old
// tests — still lands the user in the right place. This wrapper reads
// any `?tab=` (or `?flash=`) on the incoming URL, maps it to the new
// shape, and bounces to the new URL once. The destination then mounts
// the real <FamilySettingsTabs /> via the new top-level page.
//
// Wrapped in <Suspense> because useSearchParams() requires it under
// Next.js 14+ for static rendering.

import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useEffect } from 'react';

export default function FamilySettingsPage() {
  return (
    <Suspense fallback={null}>
      <FamilySettingsRedirect />
    </Suspense>
  );
}

function FamilySettingsRedirect() {
  const router = useRouter();
  const searchParams = useSearchParams();
  useEffect(() => {
    // Map legacy `?tab=...` onto `?subtab=...` so the inner
    // FamilySettingsTabs (which now reads `?subtab=`) sees the right
    // sub-tab. Preserve `?flash=` so the one-shot notice still fires.
    const next = new URLSearchParams();
    const tab = searchParams.get('tab');
    if (tab) next.set('subtab', tab);
    const flash = searchParams.get('flash');
    if (flash) next.set('flash', flash);
    next.set('tab', 'settings');
    const qs = next.toString();
    router.replace(qs ? `/family?${qs}` : '/family?tab=settings');
  }, [router, searchParams]);
  return null;
}

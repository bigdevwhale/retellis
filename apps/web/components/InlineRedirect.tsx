'use client';

// Tiny client-side redirect component used by legacy deep-link routes
// (currently /family/vault) that have been folded into the new
// /family/settings flow. Mounts, fires the navigation, renders nothing.
//
// Why not `redirect()` from next/navigation: that throws during the
// server-rendering pass and forces a full server-rendered page. The
// in-place experience is a smooth client-side swap, so we want a
// hydration-time push/replace instead.

import { useRouter } from 'next/navigation';
import { useEffect } from 'react';

export function InlineRedirect({ target, replace = true }: { target: string; replace?: boolean }) {
  const router = useRouter();
  useEffect(() => {
    if (replace) router.replace(target);
    else router.push(target);
  }, [router, target, replace]);
  return null;
}

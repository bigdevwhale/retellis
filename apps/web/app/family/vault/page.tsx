'use client';

// Deep-link backstop. The family vault unlock / setup / rotate flows
// now live inline on /family?tab=settings&subtab=key (the Family key
// sub-tab of the top-level Settings tab). This page is kept so any
// bookmarked / shared link — old tests, the prior /family/vault
// bookmark, the previous chat-no-key link — still lands the user in
// the right place. The wrapper just bounces to the new URL.

import { InlineRedirect } from '@/components/InlineRedirect';

export default function FamilyVaultPage() {
  return <InlineRedirect target="/family?tab=settings&subtab=key" />;
}

import { GuestFeature } from '@/components/GuestFeature';
import { FamilySettingsScreen } from '@/components/screens/FamilySettingsScreen';
import { cookies } from 'next/headers';

// Server component (no 'use client') so we can read the HttpOnly session cookie
// here and pass hasSession down — flash-free guest vs. real decision. The real
// FamilySettingsScreen is itself a client component; GuestFeature is the client
// wrapper that picks showcase vs. real.
export default async function FamilyPage() {
  const cookieName = process.env.NEXT_PUBLIC_SESSION_COOKIE ?? 'retellis_sess';
  const hasSession = Boolean((await cookies()).get(cookieName));
  return <GuestFeature feature="family" hasSession={hasSession} real={<FamilySettingsScreen />} />;
}

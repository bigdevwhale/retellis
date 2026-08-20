import { GuestFeature } from '@/components/GuestFeature';
import { MemoryScreen } from '@/components/screens/MemoryScreen';
import { cookies } from 'next/headers';

// Guests see the OD informational showcase; signed-in users see the real
// memory screen. `hasSession` (HttpOnly cookie presence, read server-side) is
// the flash-free signal — see app/layout.tsx + components/GuestFeature.tsx.
export default async function Page() {
  const cookieName = process.env.NEXT_PUBLIC_SESSION_COOKIE ?? 'retellis_sess';
  const hasSession = Boolean((await cookies()).get(cookieName));
  return <GuestFeature feature="memory" hasSession={hasSession} real={<MemoryScreen />} />;
}

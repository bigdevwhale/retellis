import { GuestFeature } from '@/components/GuestFeature';
import { PersonaScreen } from '@/components/screens/PersonaScreen';
import { cookies } from 'next/headers';
import { Suspense } from 'react';

export default async function Page() {
  const cookieName = process.env.NEXT_PUBLIC_SESSION_COOKIE ?? 'stillside_sess';
  const hasSession = Boolean((await cookies()).get(cookieName));
  return (
    <Suspense fallback={null}>
      <GuestFeature feature="persona" hasSession={hasSession} real={<PersonaScreen />} />
    </Suspense>
  );
}

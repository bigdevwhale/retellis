import { GuestFeature } from '@/components/GuestFeature';
import { JournalScreen } from '@/components/screens/JournalScreen';
import { cookies } from 'next/headers';

export default async function Page() {
  const cookieName = process.env.NEXT_PUBLIC_SESSION_COOKIE ?? 'stillside_sess';
  const hasSession = Boolean((await cookies()).get(cookieName));
  return <GuestFeature feature="journal" hasSession={hasSession} real={<JournalScreen />} />;
}

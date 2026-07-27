import { GuestFeature } from '@/components/GuestFeature';
import { ChatScreen } from '@/components/screens/ChatScreen';
import { cookies } from 'next/headers';

export default async function Page() {
  const cookieName = process.env.NEXT_PUBLIC_SESSION_COOKIE ?? 'stillside_sess';
  const hasSession = Boolean((await cookies()).get(cookieName));
  return <GuestFeature feature="chat" hasSession={hasSession} real={<ChatScreen />} />;
}

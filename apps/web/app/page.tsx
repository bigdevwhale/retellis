import { HomeScreen } from '@/components/screens/HomeScreen';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';

// The marketing landing at `/` is for guests. An authenticated user (session
// cookie present, read server-side — same cookie the layout reads for chrome
// selection) is redirected to the app at `/chat` before any HTML is sent, so
// there is no client-side flash of the guest page. A stale cookie still
// redirects optimistically; AuthGate then bounces a truly-revoked session to
// /login (mirrors the "stale cookie → Rail optimistically" contract).
export default async function Page() {
  const cookieStore = await cookies();
  const cookieName = process.env.NEXT_PUBLIC_SESSION_COOKIE ?? 'retellis_sess';
  if (cookieStore.get(cookieName)) {
    redirect('/chat');
  }
  return <HomeScreen />;
}

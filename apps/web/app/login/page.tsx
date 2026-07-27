import { LoginScreen } from '@/components/screens/LoginScreen';
import { Suspense } from 'react';

// /login is excluded from the cookie-presence middleware (see middleware.ts),
// so an unauthenticated browser can reach it. Suspense is required because
// LoginScreen reads `useSearchParams` (a client hook that opts into dynamic
// rendering).
export default function Page() {
  return (
    <Suspense fallback={null}>
      <LoginScreen />
    </Suspense>
  );
}

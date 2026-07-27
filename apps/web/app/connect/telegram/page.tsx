import { Suspense } from 'react';
import { ConnectTelegram } from './ConnectTelegram';

export default function Page() {
  return (
    <Suspense fallback={null}>
      <ConnectTelegram />
    </Suspense>
  );
}

import { PracticesScreen } from '@/components/screens/PracticesScreen';
import { Suspense } from 'react';

export default function Page() {
  return (
    <Suspense fallback={null}>
      <PracticesScreen />
    </Suspense>
  );
}

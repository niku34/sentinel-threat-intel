'use client';

import { useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { Shell } from './shell';
import { useInactivityLogout } from '@/hooks/useInactivityLogout';

export function ConditionalShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem('sentinel_token');
    if (!token && pathname !== '/login') {
      router.push('/login');
    } else if (token && pathname === '/login') {
      router.push('/overview');
    } else {
      setChecked(true);
    }
  }, [pathname, router]);

  // Only runs once actually authenticated and past /login — never fires an
  // unauthenticated-session logout, and resets on every route change since
  // navigation itself is real activity.
  useInactivityLogout(checked && pathname !== '/login');

  if (!checked) {
    return <div style={{ position: 'fixed', inset: 0, background: '#000' }} />;
  }

  if (pathname === '/login') return <>{children}</>;
  return <Shell>{children}</Shell>;
}

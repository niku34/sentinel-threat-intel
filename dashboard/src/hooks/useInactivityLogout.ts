'use client';

import { useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

// 20 minutes of no user interaction (click, keystroke, scroll, mouse move,
// touch) auto-logs-out and returns to /login.
//
// Deliberately based on real DOM activity only — NOT on backend events like
// a scan's websocket updates arriving. A user who starts a long-running scan
// in Live Operations and then walks away should still be logged out after
// 20 minutes; a user who is actually at the keyboard watching it will keep
// generating real activity (mouse movement, scrolling the feed, clicking
// between tabs) and will never hit the timeout, regardless of what's
// running in the background. This matches standard inactivity-timeout
// practice and avoids a security hole where leaving a scan running would
// keep a session alive indefinitely.
export const INACTIVITY_TIMEOUT_MS = 20 * 60 * 1000;

const ACTIVITY_EVENTS = [
  'mousedown', 'mousemove', 'keydown', 'scroll', 'touchstart', 'click', 'wheel',
] as const;

/**
 * Wires an inactivity timer for the lifetime of the calling component.
 * `enabled` should be false while unauthenticated / on the login page.
 * Exposed as a plain function (not a hook) so the timer-reset/logout logic
 * can be unit-tested without mounting React or a browser.
 */
export function attachInactivityLogout(
  onTimeout: () => void,
  timeoutMs: number = INACTIVITY_TIMEOUT_MS,
): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null;

  function resetTimer() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(onTimeout, timeoutMs);
  }

  resetTimer();
  for (const evt of ACTIVITY_EVENTS) {
    window.addEventListener(evt, resetTimer, { passive: true });
  }

  return function detach() {
    if (timer) clearTimeout(timer);
    for (const evt of ACTIVITY_EVENTS) {
      window.removeEventListener(evt, resetTimer);
    }
  };
}

export function useInactivityLogout(enabled: boolean) {
  const router = useRouter();
  const routerRef = useRef(router);
  routerRef.current = router;

  useEffect(() => {
    if (!enabled) return;

    async function doLogout() {
      try {
        const token = localStorage.getItem('sentinel_token');
        await fetch(`${API_URL}/api/auth/logout`, {
          method: 'POST',
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
      } catch {
        // proceed regardless — matches shell.tsx's handleSignOut behavior
      }
      localStorage.removeItem('sentinel_token');
      routerRef.current.push('/login');
    }

    return attachInactivityLogout(doLogout);
  }, [enabled]);
}

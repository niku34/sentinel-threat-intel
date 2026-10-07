'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { ScanModal } from './scan-modal';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

const MONO: React.CSSProperties = { fontFamily: "'Space Mono', monospace" };

type NavItem = {
  href: string;
  label: string;
  module?: 'kaaval' | 'kavasam' | 'veriff';
};

type NavSection = {
  label: string;
  items: NavItem[];
};

const NAV_SECTIONS: NavSection[] = [
  {
    label: 'Platform',
    items: [
      { href: '/overview', label: 'Overview' },
      { href: '/live-operations', label: 'Live Operations' },
      { href: '/findings', label: 'Findings' },
    ],
  },
  {
    label: 'Modules',
    items: [
      { href: '/kaaval', label: 'Kaaval AI', module: 'kaaval' },
      { href: '/kavasam', label: 'Digital Kavasam', module: 'kavasam' },
      { href: '/veriff', label: 'Veriff', module: 'veriff' },
    ],
  },
  {
    label: 'Intelligence',
    items: [
      { href: '/llm-wiki', label: 'Assistant' },
      { href: '/plugins', label: 'Plugin Marketplace' },
    ],
  },
  {
    label: 'Administration',
    items: [
      { href: '/admin', label: 'Admin Console' },
      { href: '/users', label: 'User Management' },
      { href: '/security', label: 'Security & Access' },
      { href: '/health', label: 'Module Health' },
      { href: '/audit', label: 'Audit Log' },
      { href: '/accuracy', label: 'Accuracy' },
    ],
  },
];

const ALL_ITEMS: NavItem[] = NAV_SECTIONS.flatMap((section) => section.items);

const USER = {
  initial: 'N',
  name: 'Nitin Pranav',
  role: 'Super Admin',
  email: 'admin@example.com',
};

function activeFor(pathname: string): NavItem {
  const match = ALL_ITEMS.find(
    (item) => pathname === item.href || pathname.startsWith(`${item.href}/`),
  );
  return match ?? ALL_ITEMS[0];
}

// ── Toast ──────────────────────────────────────────────────────────────────

function Toast({ message, type }: { message: string; type: 'success' | 'error' }) {
  return (
    <div style={{
      position: 'fixed', bottom: '24px', right: '24px', zIndex: 200,
      padding: '12px 20px',
      background: type === 'success' ? 'rgba(74,222,128,0.12)' : 'rgba(255,56,56,0.12)',
      border: `1px solid ${type === 'success' ? 'rgba(74,222,128,0.4)' : 'rgba(255,56,56,0.4)'}`,
      color: type === 'success' ? 'var(--safe)' : 'var(--critical)',
      ...MONO, fontSize: '11px', letterSpacing: '0.5px',
    }}>
      {message}
    </div>
  );
}

// ── Change Password Modal ──────────────────────────────────────────────────

function ChangePasswordModal({
  onClose,
  onToast,
}: {
  onClose: () => void;
  onToast: (msg: string, type: 'success' | 'error') => void;
}) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const [currentPw, setCurrentPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [confirmPw, setConfirmPw] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') onClose(); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  function handleOverlayClick(e: React.MouseEvent) {
    if (e.target === overlayRef.current) onClose();
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    if (newPw !== confirmPw) { setErr('New passwords do not match.'); return; }
    if (newPw.length < 6) { setErr('New password must be at least 6 characters.'); return; }
    setBusy(true);
    try {
      const token = localStorage.getItem('sentinel_token');
      const res = await fetch(`${API_URL}/api/auth/change-password`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ current_password: currentPw, new_password: newPw }),
      });
      if (res.ok) {
        onToast('Password changed successfully.', 'success');
        onClose();
      } else {
        const data = await res.json().catch(() => ({}));
        setErr(data.detail ?? 'Password change failed.');
      }
    } catch {
      setErr('Network error. Please try again.');
    } finally {
      setBusy(false);
    }
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', boxSizing: 'border-box',
    background: 'var(--bg)', border: '1px solid var(--line)',
    color: 'var(--text)', ...MONO, fontSize: '12px',
    padding: '10px 12px', outline: 'none',
  };

  const labelStyle: React.CSSProperties = {
    ...MONO, fontSize: '9px', color: '#555',
    letterSpacing: '2px', textTransform: 'uppercase',
    display: 'block', marginBottom: '6px',
  };

  return (
    <div
      ref={overlayRef}
      onClick={handleOverlayClick}
      style={{
        position: 'fixed', inset: 0, zIndex: 150,
        background: 'rgba(0,0,0,0.85)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '24px',
      }}
    >
      <div style={{
        background: '#0a0a0a', border: '1px solid #1f1f1f',
        width: '100%', maxWidth: '420px', padding: '40px', position: 'relative',
      }}>
        <button
          onClick={onClose}
          style={{
            position: 'absolute', top: '16px', right: '16px',
            background: 'none', border: 'none', color: '#555',
            fontSize: '18px', cursor: 'pointer', lineHeight: 1, padding: '4px 8px',
          }}
        >
          ✕
        </button>

        <div style={{ marginBottom: '28px' }}>
          <div style={{ ...MONO, fontSize: '9px', color: '#444', letterSpacing: '3px', textTransform: 'uppercase', marginBottom: '8px' }}>
            SECURITY
          </div>
          <div style={{ fontSize: '18px', fontWeight: 600, color: 'var(--text)' }}>
            Change Password
          </div>
          <div style={{ width: '32px', height: '1px', background: 'var(--accent)', marginTop: '10px' }} />
        </div>

        <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          <div>
            <label style={labelStyle}>Current Password</label>
            <input
              type="password"
              value={currentPw}
              onChange={(e) => setCurrentPw(e.target.value)}
              required
              style={inputStyle}
              autoFocus
            />
          </div>
          <div>
            <label style={labelStyle}>New Password</label>
            <input
              type="password"
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              required
              style={inputStyle}
            />
          </div>
          <div>
            <label style={labelStyle}>Confirm New Password</label>
            <input
              type="password"
              value={confirmPw}
              onChange={(e) => setConfirmPw(e.target.value)}
              required
              style={inputStyle}
            />
          </div>

          {err && (
            <div style={{ ...MONO, fontSize: '10px', color: 'var(--critical)', letterSpacing: '0.5px' }}>
              ✕ {err}
            </div>
          )}

          <div style={{ display: 'flex', gap: '10px', marginTop: '4px' }}>
            <button
              type="submit"
              disabled={busy}
              style={{
                flex: 1, padding: '10px',
                background: busy ? 'transparent' : 'rgba(255,91,58,0.12)',
                border: '1px solid rgba(255,91,58,0.4)',
                color: 'var(--accent)',
                ...MONO, fontSize: '11px', letterSpacing: '1px', cursor: busy ? 'wait' : 'pointer',
                opacity: busy ? 0.6 : 1,
              }}
            >
              {busy ? 'UPDATING…' : 'UPDATE PASSWORD'}
            </button>
            <button
              type="button"
              onClick={onClose}
              style={{
                padding: '10px 16px',
                background: 'transparent',
                border: '1px solid var(--line)',
                color: 'var(--text-mid)',
                ...MONO, fontSize: '11px', letterSpacing: '1px', cursor: 'pointer',
              }}
            >
              CANCEL
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Profile Dropdown ───────────────────────────────────────────────────────

function ProfileDropdown({
  onClose,
  onChangePassword,
  onSignOut,
}: {
  onClose: () => void;
  onChangePassword: () => void;
  onSignOut: () => void;
}) {
  const router = useRouter();
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleMouseDown(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        onClose();
      }
    }
    document.addEventListener('mousedown', handleMouseDown);
    return () => document.removeEventListener('mousedown', handleMouseDown);
  }, [onClose]);

  const menuItemStyle: React.CSSProperties = {
    display: 'flex', alignItems: 'center', gap: '10px',
    padding: '10px 16px', cursor: 'pointer',
    ...MONO, fontSize: '11px', letterSpacing: '0.5px',
    color: 'var(--text-mid)', background: 'transparent', border: 'none',
    width: '100%', textAlign: 'left',
    transition: 'background 0.1s',
  };

  return (
    <div
      ref={dropdownRef}
      style={{
        position: 'absolute', bottom: 'calc(100% + 8px)', left: 0, right: 0,
        background: '#0d0d0d',
        border: '1px solid var(--line)',
        zIndex: 100,
        boxShadow: '0 -8px 24px rgba(0,0,0,0.6)',
      }}
    >
      {/* User info — display only */}
      <div style={{ padding: '16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{
            width: '36px', height: '36px', borderRadius: '50%',
            background: 'rgba(255,91,58,0.15)', border: '1px solid rgba(255,91,58,0.3)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            ...MONO, fontSize: '14px', color: 'var(--accent)', flexShrink: 0,
          }}>
            {USER.initial}
          </div>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {USER.name}
            </div>
            <div style={{ ...MONO, fontSize: '9px', color: '#555', letterSpacing: '1px', textTransform: 'uppercase', marginTop: '2px' }}>
              {USER.role}
            </div>
            <div style={{ ...MONO, fontSize: '10px', color: 'var(--dim)', marginTop: '2px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {USER.email}
            </div>
          </div>
        </div>
      </div>

      <div style={{ height: '1px', background: 'var(--line)' }} />

      <button
        style={menuItemStyle}
        onClick={() => { onClose(); onChangePassword(); }}
        onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.04)'; }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
      >
        <span style={{ color: '#555' }}>⌘</span> Change Password
      </button>

      <button
        style={menuItemStyle}
        onClick={() => { onClose(); router.push('/security'); }}
        onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.04)'; }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
      >
        <span style={{ color: '#555' }}>⊙</span> Active Sessions
      </button>

      <div style={{ height: '1px', background: 'var(--line)' }} />

      <button
        style={{ ...menuItemStyle, color: 'var(--critical)' }}
        onClick={onSignOut}
        onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = 'rgba(255,56,56,0.06)'; }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
      >
        <span>→</span> Sign Out
      </button>
    </div>
  );
}

// ── Notifications Panel ────────────────────────────────────────────────────

interface Notification {
  id: string;
  source: string;
  title: string;
  subtitle: string;
  severity: string;
  detected_at: string | null;
  read: boolean;
}

const SEV_COLOR: Record<string, string> = {
  CRITICAL: '#ff3838',
  HIGH: '#ff8b3a',
  MEDIUM: '#ffc83a',
  LOW: '#4ade80',
};

const SRC_COLOR: Record<string, string> = {
  KAAVAL:   '#ff5b3a',
  KAVASAM:  '#3ab0ff',
  VERIFF:   '#4ade80',
  SECURITY: '#ff3838',
  ADMIN:    '#ffc83a',
  SYSTEM:   '#666',
};

const SRC_NAV: Record<string, string> = {
  KAAVAL:   '/findings',
  KAVASAM:  '/kavasam',
  VERIFF:   '/veriff',
  SECURITY: '/security',
  ADMIN:    '/admin',
  SYSTEM:   '/live-operations',
};

function timeAgo(iso: string | null): string {
  if (!iso) return '—';
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function isToday(iso: string | null): boolean {
  if (!iso) return false;
  const d = new Date(iso);
  const now = new Date();
  return d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
}

function GroupLabel({ label }: { label: string }) {
  return (
    <div style={{
      padding: '8px 20px 4px',
      ...MONO, fontSize: '8px', letterSpacing: '2px',
      color: '#444', borderBottom: '1px solid var(--line)',
      background: '#080808',
    }}>
      {label}
    </div>
  );
}

function NotificationsPanel({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const panelRef = useRef<HTMLDivElement>(null);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [unread, setUnread] = useState(0);

  async function fetchNotifs() {
    try {
      const res = await fetch(`${API_URL}/api/notifications`);
      if (!res.ok) return;
      const data = await res.json();
      setNotifications(data.notifications ?? []);
      setUnread(data.unread_count ?? 0);
    } catch {
      // silent — panel still opens
    }
  }

  useEffect(() => {
    fetchNotifs();
    const timer = setInterval(fetchNotifs, 30000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    function handleMouseDown(e: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose();
      }
    }
    document.addEventListener('mousedown', handleMouseDown);
    return () => document.removeEventListener('mousedown', handleMouseDown);
  }, [onClose]);

  async function markRead(n: Notification) {
    await fetch(`${API_URL}/api/notifications/${n.id}/read`, { method: 'POST' }).catch(() => {});
    setNotifications((prev) => prev.map((x) => x.id === n.id ? { ...x, read: true } : x));
    setUnread((c) => Math.max(0, c - 1));
    router.push(SRC_NAV[n.source] ?? '/findings');
    onClose();
  }

  async function markAllRead() {
    await fetch(`${API_URL}/api/notifications/read-all`, { method: 'POST' }).catch(() => {});
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
    setUnread(0);
  }

  const todayItems = notifications.filter((n) => isToday(n.detected_at));
  const olderItems = notifications.filter((n) => !isToday(n.detected_at));

  function NotifRow({ n }: { n: Notification }) {
    const srcColor = SRC_COLOR[n.source] ?? '#666';
    const sevColor = SEV_COLOR[n.severity] ?? '#555';
    return (
      <button
        key={n.id}
        onClick={() => markRead(n)}
        style={{
          display: 'flex', alignItems: 'flex-start', gap: '12px',
          padding: '13px 20px', width: '100%', textAlign: 'left',
          background: n.read ? 'transparent' : 'rgba(255,255,255,0.02)',
          border: 'none', borderBottom: '1px solid var(--line)',
          cursor: 'pointer',
        }}
        onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.04)'; }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = n.read ? 'transparent' : 'rgba(255,255,255,0.02)'; }}
      >
        {/* Severity dot */}
        <div style={{
          width: '7px', height: '7px', borderRadius: '50%', flexShrink: 0, marginTop: '5px',
          background: sevColor,
          boxShadow: n.read ? 'none' : `0 0 5px ${sevColor}`,
        }} />

        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            fontSize: '12px', color: n.read ? 'var(--text-mid)' : 'var(--text)',
            fontWeight: n.read ? 400 : 500, lineHeight: 1.4, marginBottom: '5px',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>
            {n.title}
          </div>
          <div style={{ display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' }}>
            {/* Source badge */}
            <span style={{
              ...MONO, fontSize: '7px', letterSpacing: '1px',
              color: srcColor, border: `1px solid ${srcColor}`,
              padding: '1px 5px', opacity: 0.9, flexShrink: 0,
            }}>
              {n.source}
            </span>
            {/* Subtitle */}
            <span style={{
              ...MONO, fontSize: '9px', color: 'var(--dim)',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              {n.subtitle}
            </span>
            <span style={{ ...MONO, fontSize: '9px', color: '#444', flexShrink: 0 }}>
              {timeAgo(n.detected_at)}
            </span>
          </div>
        </div>

        {/* Unread dot */}
        {!n.read && (
          <div style={{
            width: '5px', height: '5px', borderRadius: '50%',
            background: '#ff3838', flexShrink: 0, marginTop: '6px',
          }} />
        )}
      </button>
    );
  }

  return (
    <div
      ref={panelRef}
      style={{
        position: 'fixed', top: 0, right: 0, bottom: 0, width: '400px', zIndex: 120,
        background: '#0a0a0a', borderLeft: '1px solid var(--line)',
        display: 'flex', flexDirection: 'column',
        boxShadow: '-8px 0 32px rgba(0,0,0,0.6)',
      }}
    >
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '20px 20px 16px', borderBottom: '1px solid var(--line)', flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <div style={{ ...MONO, fontSize: '11px', letterSpacing: '2px', color: 'var(--text)' }}>
            NOTIFICATIONS
          </div>
          {unread > 0 && (
            <div style={{
              background: '#ff3838', color: '#fff', borderRadius: '10px',
              ...MONO, fontSize: '9px', padding: '2px 7px', letterSpacing: '0.5px',
            }}>
              {unread}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {unread > 0 && (
            <button
              onClick={markAllRead}
              style={{
                background: 'none', border: 'none', cursor: 'pointer',
                ...MONO, fontSize: '9px', color: 'var(--dim)', letterSpacing: '1px',
                textDecoration: 'underline', textUnderlineOffset: '3px',
              }}
            >
              MARK ALL READ
            </button>
          )}
          <button
            onClick={onClose}
            style={{
              background: 'none', border: 'none', color: '#555',
              fontSize: '18px', cursor: 'pointer', lineHeight: 1, padding: '2px 6px',
            }}
          >
            ✕
          </button>
        </div>
      </div>

      {/* List */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {notifications.length === 0 ? (
          <div style={{
            padding: '48px 20px', textAlign: 'center',
            ...MONO, fontSize: '11px', color: 'var(--dim)', letterSpacing: '1px',
          }}>
            NO NOTIFICATIONS
          </div>
        ) : (
          <>
            {todayItems.length > 0 && (
              <>
                <GroupLabel label="TODAY" />
                {todayItems.map((n) => <NotifRow key={n.id} n={n} />)}
              </>
            )}
            {olderItems.length > 0 && (
              <>
                <GroupLabel label="LAST 7 DAYS" />
                {olderItems.map((n) => <NotifRow key={n.id} n={n} />)}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ── Shell ──────────────────────────────────────────────────────────────────

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const active = activeFor(pathname);
  const [scanOpen, setScanOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [changePwOpen, setChangePwOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const [unreadCount, setUnreadCount] = useState(0);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

  // Poll unread count for the badge — lightweight, separate from the panel fetch
  useEffect(() => {
    async function pollUnread() {
      try {
        const res = await fetch(`${API_URL}/api/notifications`);
        if (res.ok) {
          const data = await res.json();
          setUnreadCount(data.unread_count ?? 0);
        }
      } catch {
        // silent
      }
    }
    pollUnread();
    const timer = setInterval(pollUnread, 30000);
    return () => clearInterval(timer);
  }, []);

  function showToast(message: string, type: 'success' | 'error') {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3500);
  }

  async function handleSignOut() {
    setProfileOpen(false);
    try {
      const token = localStorage.getItem('sentinel_token');
      await fetch(`${API_URL}/api/auth/logout`, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
    } catch {
      // proceed regardless
    }
    localStorage.removeItem('sentinel_token');
    router.push('/login');
  }

  return (
    <>
      <aside className="sidebar">
        <div className="logo">
          <div className="logo-mark">
            BROADCASTER<span>SHIELD</span>
          </div>
          <div className="logo-sub">v1.0 · Console</div>
        </div>

        {NAV_SECTIONS.map((section) => (
          <div className="nav-section" key={section.label}>
            <div className="nav-label">{section.label}</div>
            {section.items.map((item) => {
              const isActive = item.href === active.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`nav-item${isActive ? ' active' : ''}`}
                  data-module={item.module}
                >
                  <span className="nav-dot" />
                  {item.label}
                </Link>
              );
            })}
          </div>
        ))}

        <div className="sidebar-footer" style={{ position: 'relative' }}>
          {profileOpen && (
            <ProfileDropdown
              onClose={() => setProfileOpen(false)}
              onChangePassword={() => setChangePwOpen(true)}
              onSignOut={handleSignOut}
            />
          )}

          <div
            className="user-row"
            onClick={() => setProfileOpen((v) => !v)}
            style={{ cursor: 'pointer', userSelect: 'none' }}
          >
            <div className="user-avatar">{USER.initial}</div>
            <div className="user-info">
              <div className="user-name">{USER.name}</div>
              <div className="user-role">{USER.role}</div>
            </div>
            <div style={{
              marginLeft: 'auto', ...MONO, fontSize: '10px',
              color: '#444', paddingRight: '2px',
              transform: profileOpen ? 'rotate(180deg)' : 'rotate(0deg)',
              transition: 'transform 0.15s',
            }}>
              ▲
            </div>
          </div>
        </div>
      </aside>

      <main className="main">
        <div className="topbar">
          <div className="breadcrumb">
            BROADCASTER SHIELD <span>·</span> {active.label.toUpperCase()}
          </div>
          <div className="topbar-actions">
            <div className="status-pill">
              <span className="status-dot" /> All Modules Operational
            </div>
            <button
              className="btn btn-ghost"
              type="button"
              onClick={() => setNotifOpen((v) => !v)}
              style={{ position: 'relative' }}
            >
              Notifications
              {unreadCount > 0 && (
                <span style={{
                  position: 'absolute', top: '4px', right: '4px',
                  width: '7px', height: '7px', borderRadius: '50%',
                  background: '#ff3838',
                  boxShadow: '0 0 6px #ff3838',
                }} />
              )}
            </button>
            <button className="btn" type="button" onClick={() => setScanOpen(true)}>
              + New Scan
            </button>
            {scanOpen && <ScanModal onClose={() => setScanOpen(false)} />}
          </div>
        </div>
        {children}
      </main>

      {changePwOpen && (
        <ChangePasswordModal
          onClose={() => setChangePwOpen(false)}
          onToast={showToast}
        />
      )}

      {notifOpen && (
        <NotificationsPanel onClose={() => setNotifOpen(false)} />
      )}

      {toast && <Toast message={toast.message} type={toast.type} />}
    </>
  );
}

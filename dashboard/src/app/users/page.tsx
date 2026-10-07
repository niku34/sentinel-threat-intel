'use client';

import { useEffect, useRef, useState } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
const MONO: React.CSSProperties = { fontFamily: "'Space Mono', monospace" };
const CURRENT_USER_EMAIL = 'admin@example.com';

// ── Types ──────────────────────────────────────────────────────────────────

interface User {
  id: string;
  name: string;
  email: string;
  role: string;
  is_active: boolean;
  created_at: string;
  last_login: string | null;
}

type Cap = 'yes' | 'no' | 'partial';
interface Capability {
  name: string;
  superAdmin: Cap; editor: Cap; itAdmin: Cap; journalist: Cap; viewer: Cap;
}

// ── Role config ────────────────────────────────────────────────────────────

const ROLES = ['SUPER_ADMIN', 'EDITOR', 'IT_ADMIN', 'JOURNALIST', 'VIEWER'] as const;

const ROLE_COLOR: Record<string, string> = {
  SUPER_ADMIN: 'var(--accent)',
  IT_ADMIN:    'var(--blue)',
  EDITOR:      'var(--safe)',
  JOURNALIST:  'var(--text-mid)',
  VIEWER:      'var(--text-mid)',
};

const ROLE_LABEL: Record<string, string> = {
  SUPER_ADMIN: 'SUPER ADMIN',
  IT_ADMIN:    'IT ADMIN',
  EDITOR:      'EDITOR',
  JOURNALIST:  'JOURNALIST',
  VIEWER:      'VIEWER',
};

const ROLE_DESC: Record<string, string> = {
  SUPER_ADMIN: 'Full access',
  IT_ADMIN:    'Kavasam · Full control · Alert routing',
  EDITOR:      'Kaaval + Veriff · Read + Action',
  JOURNALIST:  'Veriff only',
  VIEWER:      'Audit logs + CERT-In drafts · Read only',
};

// ── Permission matrix ──────────────────────────────────────────────────────

const CAPABILITIES: Capability[] = [
  { name: 'View Findings',       superAdmin: 'yes', editor: 'yes', itAdmin: 'yes', journalist: 'partial', viewer: 'yes' },
  { name: 'Act on Findings',     superAdmin: 'yes', editor: 'yes', itAdmin: 'yes', journalist: 'no',      viewer: 'no' },
  { name: 'Configure Modules',   superAdmin: 'yes', editor: 'no',  itAdmin: 'yes', journalist: 'no',      viewer: 'no' },
  { name: 'Add / Remove Users',  superAdmin: 'yes', editor: 'no',  itAdmin: 'no',  journalist: 'no',      viewer: 'no' },
  { name: 'Rotate API Keys',     superAdmin: 'yes', editor: 'no',  itAdmin: 'yes', journalist: 'no',      viewer: 'no' },
  { name: 'File CERT-In Reports',superAdmin: 'yes', editor: 'no',  itAdmin: 'yes', journalist: 'no',      viewer: 'no' },
  { name: 'Use Veriff',    superAdmin: 'yes', editor: 'yes', itAdmin: 'no',  journalist: 'yes',     viewer: 'no' },
  { name: 'Install Plugins',     superAdmin: 'yes', editor: 'no',  itAdmin: 'partial', journalist: 'no',  viewer: 'no' },
  { name: 'Master Kill Switch',  superAdmin: 'yes', editor: 'no',  itAdmin: 'no',  journalist: 'no',      viewer: 'no' },
  { name: 'View Audit Log',      superAdmin: 'yes', editor: 'no',  itAdmin: 'yes', journalist: 'no',      viewer: 'yes' },
];

function MatrixCell({ value }: { value: Cap }) {
  if (value === 'yes')     return <td className="yes">✓</td>;
  if (value === 'partial') return <td className="partial">◐</td>;
  return <td className="no">—</td>;
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

// ── Add User Modal ─────────────────────────────────────────────────────────

function AddUserModal({
  onClose,
  onSuccess,
}: {
  onClose: () => void;
  onSuccess: (u: User) => void;
}) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<string>('JOURNALIST');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') onClose(); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/users`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, email, role, password }),
      });
      const data = await res.json();
      if (!res.ok) { setErr(data.detail ?? 'Failed to create user.'); return; }
      onSuccess(data as User);
      onClose();
    } catch {
      setErr('Network error.');
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
    ...MONO, fontSize: '9px', color: '#555', letterSpacing: '2px',
    textTransform: 'uppercase', display: 'block', marginBottom: '6px',
  };

  return (
    <div
      ref={overlayRef}
      onClick={(e) => { if (e.target === overlayRef.current) onClose(); }}
      style={{
        position: 'fixed', inset: 0, zIndex: 150,
        background: 'rgba(0,0,0,0.85)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px',
      }}
    >
      <div style={{
        background: '#0a0a0a', border: '1px solid #1f1f1f',
        width: '100%', maxWidth: '440px', padding: '40px', position: 'relative',
      }}>
        <button onClick={onClose} style={{
          position: 'absolute', top: '16px', right: '16px',
          background: 'none', border: 'none', color: '#555',
          fontSize: '18px', cursor: 'pointer', lineHeight: 1, padding: '4px 8px',
        }}>✕</button>

        <div style={{ marginBottom: '28px' }}>
          <div style={{ ...MONO, fontSize: '9px', color: '#444', letterSpacing: '3px', marginBottom: '8px' }}>
            USER MANAGEMENT
          </div>
          <div style={{ fontSize: '18px', fontWeight: 600, color: 'var(--text)' }}>Add Team Member</div>
          <div style={{ width: '32px', height: '1px', background: 'var(--accent)', marginTop: '10px' }} />
        </div>

        <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
          <div>
            <label style={labelStyle}>Full Name</label>
            <input value={name} onChange={(e) => setName(e.target.value)} required style={inputStyle} autoFocus />
          </div>
          <div>
            <label style={labelStyle}>Email</label>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>Role</label>
            <select value={role} onChange={(e) => setRole(e.target.value)} style={{ ...inputStyle, cursor: 'pointer' }}>
              {ROLES.map((r) => (
                <option key={r} value={r}>{ROLE_LABEL[r]}</option>
              ))}
            </select>
          </div>
          <div>
            <label style={labelStyle}>Password</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required style={inputStyle} placeholder="Min 6 characters" />
          </div>

          {err && (
            <div style={{ ...MONO, fontSize: '10px', color: 'var(--critical)', letterSpacing: '0.5px' }}>
              ✕ {err}
            </div>
          )}

          <div style={{ display: 'flex', gap: '10px', marginTop: '4px' }}>
            <button type="submit" disabled={busy} style={{
              flex: 1, padding: '10px',
              background: busy ? 'transparent' : 'rgba(255,91,58,0.12)',
              border: '1px solid rgba(255,91,58,0.4)',
              color: 'var(--accent)', ...MONO, fontSize: '11px', letterSpacing: '1px',
              cursor: busy ? 'wait' : 'pointer', opacity: busy ? 0.6 : 1,
            }}>
              {busy ? 'ADDING…' : 'ADD USER'}
            </button>
            <button type="button" onClick={onClose} style={{
              padding: '10px 16px', background: 'transparent',
              border: '1px solid var(--line)', color: 'var(--text-mid)',
              ...MONO, fontSize: '11px', letterSpacing: '1px', cursor: 'pointer',
            }}>
              CANCEL
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [addOpen, setAddOpen] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
  const [busy, setBusy] = useState<Record<string, boolean>>({});

  function showToast(message: string, type: 'success' | 'error') {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3500);
  }

  async function loadUsers() {
    try {
      const res = await fetch(`${API_URL}/api/users`);
      if (res.ok) {
        const data = await res.json();
        setUsers(data.users ?? []);
      }
    } catch { /* keep stale */ }
    finally { setLoading(false); }
  }

  useEffect(() => { loadUsers(); }, []);

  async function toggleActive(user: User) {
    const action = user.is_active ? 'deactivate' : 'activate';
    setBusy((b) => ({ ...b, [user.id]: true }));
    try {
      const res = await fetch(`${API_URL}/api/users/${user.id}/${action}`, { method: 'PATCH' });
      const data = await res.json();
      if (!res.ok) { showToast(data.detail ?? 'Action failed.', 'error'); return; }
      setUsers((prev) => prev.map((u) => u.id === user.id ? { ...u, is_active: !user.is_active } : u));
      showToast(`${user.name} ${action}d.`, 'success');
    } catch {
      showToast('Network error.', 'error');
    } finally {
      setBusy((b) => ({ ...b, [user.id]: false }));
    }
  }

  const total       = users.length;
  const superAdmins = users.filter((u) => u.role === 'SUPER_ADMIN').length;
  const activeNow   = users.filter((u) => u.is_active).length;

  // Active users first, then by created_at ascending
  const roster = [...users].sort((a, b) => {
    if (a.is_active !== b.is_active) return a.is_active ? -1 : 1;
    return a.created_at.localeCompare(b.created_at);
  });

  return (
    <div className="page" id="page-users">
      <div className="page-header">
        <div className="page-label">USER MANAGEMENT</div>
        <h1 className="page-title med">
          Role-based access.
          <br />
          <span className="accent">Editor. Journalist. Admin.</span>
        </h1>
        <p className="page-sub">
          Granular permissions per module. Add and remove users. Audit who can see
          what. DPDP-compliant by design.
        </p>
      </div>

      <div className="metrics">
        <div className="metric">
          <div className="metric-label">Total Users</div>
          <div className="metric-value accent">{loading ? '…' : total}</div>
          <div className="metric-delta">Team roster</div>
        </div>
        <div className="metric">
          <div className="metric-label">Super Admins</div>
          <div className="metric-value">{loading ? '…' : superAdmins}</div>
          <div className="metric-delta">Admins</div>
        </div>
        <div className="metric">
          <div className="metric-label">Active Now</div>
          <div className="metric-value safe">{loading ? '…' : activeNow}</div>
          <div className="metric-delta safe">Active accounts</div>
        </div>
        <div className="metric">
          <div className="metric-label">MFA</div>
          <div className="metric-value critical">NOT ENABLED</div>
          <div className="metric-delta critical">Login is password-only</div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Team Roster</div>
          <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
            <div className="panel-tag">Role-based · DPDP compliant</div>
            <button
              onClick={() => setAddOpen(true)}
              style={{
                ...MONO, fontSize: '10px', letterSpacing: '1px', cursor: 'pointer',
                padding: '6px 14px',
                background: 'rgba(255,91,58,0.12)',
                border: '1px solid rgba(255,91,58,0.4)',
                color: 'var(--accent)',
              }}
            >
              + ADD USER
            </button>
          </div>
        </div>
        <div className="panel-body" style={{ padding: 0 }}>
          <div className="plugin-list">
            {loading && (
              <div className="plugin-row" style={{ justifyContent: 'center' }}>
                <span style={{ ...MONO, fontSize: '11px', color: 'var(--dim)' }}>Loading…</span>
              </div>
            )}
            {roster.map((user) => {
              const isSelf = user.email === CURRENT_USER_EMAIL;
              const color = ROLE_COLOR[user.role] ?? 'var(--text-mid)';
              return (
                <div
                  className="plugin-row"
                  key={user.id}
                  style={{ opacity: user.is_active ? 1 : 0.4 }}
                >
                  <div className="plugin-name">
                    {user.name}{' '}
                    <span className="dim">
                      · {user.email} · {ROLE_DESC[user.role] ?? user.role}
                      {!user.is_active && ' · DEACTIVATED'}
                    </span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexShrink: 0 }}>
                    <div style={{ ...MONO, fontSize: '11px', color }}>
                      ● {ROLE_LABEL[user.role] ?? user.role}
                    </div>
                    {!isSelf && (
                      <button
                        onClick={() => toggleActive(user)}
                        disabled={busy[user.id]}
                        style={{
                          ...MONO, fontSize: '9px', letterSpacing: '1px',
                          cursor: busy[user.id] ? 'wait' : 'pointer',
                          padding: '3px 10px',
                          background: 'transparent',
                          border: `1px solid ${user.is_active ? 'var(--line)' : 'rgba(74,222,128,0.3)'}`,
                          color: user.is_active ? 'var(--dim)' : 'var(--safe)',
                          opacity: busy[user.id] ? 0.5 : 1,
                        }}
                      >
                        {busy[user.id] ? '…' : user.is_active ? 'DEACTIVATE' : 'ACTIVATE'}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Permission Matrix</div>
          <div className="panel-tag">Role × Capability · At-a-glance access map</div>
        </div>
        <div className="panel-body" style={{ padding: 0 }}>
          <table className="matrix-table">
            <thead>
              <tr>
                <th>Capability</th>
                <th>Super Admin</th>
                <th>Editor</th>
                <th>IT Admin</th>
                <th>Journalist</th>
                <th>Viewer</th>
              </tr>
            </thead>
            <tbody>
              {CAPABILITIES.map((c) => (
                <tr key={c.name}>
                  <td>{c.name}</td>
                  <MatrixCell value={c.superAdmin} />
                  <MatrixCell value={c.editor} />
                  <MatrixCell value={c.itAdmin} />
                  <MatrixCell value={c.journalist} />
                  <MatrixCell value={c.viewer} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <p style={{
        fontSize: '11px', color: 'var(--text-dim)',
        fontFamily: "'Space Mono', monospace",
        letterSpacing: '1px', marginTop: '-12px',
      }}>
        ✓ FULL · ◐ LIMITED · — NO ACCESS
      </p>

      {addOpen && (
        <AddUserModal
          onClose={() => setAddOpen(false)}
          onSuccess={(u) => {
            setUsers((prev) => [u, ...prev]);
            showToast(`${u.name} added as ${ROLE_LABEL[u.role]}.`, 'success');
          }}
        />
      )}

      {toast && <Toast message={toast.message} type={toast.type} />}
    </div>
  );
}

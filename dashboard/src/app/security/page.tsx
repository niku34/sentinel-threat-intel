'use client';

import { useEffect, useState } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
const MONO: React.CSSProperties = { fontFamily: "'Space Mono', monospace" };

// ── Types ──────────────────────────────────────────────────────────────────

interface Session {
  user_id: string;
  name: string;
  role: string;
  email: string;
  ip: string;
  device: string;
  browser: string;
  login_at: string;
  is_current: boolean;
}

interface LoginEvent {
  user: string;
  ip: string;
  action: string;
  status: 'OK' | 'BLOCKED';
  timestamp: string | null;
}

interface PostureCheck {
  status: boolean | null;   // null = not a system check
  verified: boolean;        // false = could not be confirmed, never show as a pass
  note: string;
}

type PostureKey =
  | 'encryption_at_rest'
  | 'encryption_in_transit'
  | 'mfa_mandatory'
  | 'audit_retention_180d'
  | 'tamper_proof_logging'
  | 'api_key_rotation'
  | 'rbac'
  | 'pentest_scheduled';

type Posture = Record<PostureKey, PostureCheck>;

// ── Static data ────────────────────────────────────────────────────────────

interface PostureItem {
  key: PostureKey;
  title: string;
  desc: string;  // fallback only — the live note from the API is preferred
}

const POSTURE_ITEMS: PostureItem[] = [
  { key: 'encryption_at_rest',    title: 'Data Encrypted at Rest',     desc: 'Checking…' },
  { key: 'encryption_in_transit', title: 'Data Encrypted in Transit',  desc: 'Checking…' },
  { key: 'mfa_mandatory',         title: 'MFA Mandatory',              desc: 'Checking…' },
  { key: 'audit_retention_180d',  title: 'Audit Retention',            desc: 'Checking…' },
  { key: 'tamper_proof_logging',  title: 'Tamper-Proof Logging',       desc: 'Checking…' },
  { key: 'api_key_rotation',      title: 'API Key Rotation',           desc: 'Checking…' },
  { key: 'rbac',                  title: 'Role-Based Access Control',  desc: 'Checking…' },
  { key: 'pentest_scheduled',     title: 'Penetration Test',           desc: 'Tracked externally' },
];

// Where data actually lives and what actually leaves the network. Mirrors the
// data-flow table in infrastructure/DPDP-compliance.md — keep the two in sync.
const RESIDENCY = [
  { name: 'Tamil NLP & LLM inference',  detail: '· Qwen 2.5 14B via Ollama · nothing sent to any external AI provider', location: '● ON PREMISES', color: 'var(--safe)' },
  { name: 'Audit Log + DPDP Records',   detail: '· PostgreSQL · WORM-protected',      location: '● ON PREMISES', color: 'var(--safe)' },
  { name: 'Threats & Findings Database', detail: '· PostgreSQL',                      location: '● ON PREMISES', color: 'var(--safe)' },
  { name: 'Voice fingerprints',         detail: '· anchor_voice_profiles',            location: '● ON PREMISES', color: 'var(--safe)' },
  { name: 'Fact-check queries',         detail: '· claim text sent as a search query to PIB, AltNews, Vishvas, FactCrescendo Tamil, IndiaToday, QuintWebQoof, Newschecker Tamil', location: '● LEAVES NETWORK', color: 'var(--high)' },
  { name: 'Google News / Google Search', detail: '· claim-derived search terms',      location: '● LEAVES NETWORK', color: 'var(--high)' },
  { name: 'YouTube Data API',           detail: '· channel IDs · returns public counts only', location: '● LEAVES NETWORK', color: 'var(--high)' },
  { name: 'Meta Graph API',             detail: '· page / Instagram account IDs · only when Meta integration is configured', location: '● LEAVES NETWORK', color: 'var(--high)' },
  { name: 'Embedding model download',   detail: '· HuggingFace · weights only, first run, cached after', location: '● LEAVES NETWORK', color: 'var(--medium)' },
];

// ── Helpers ────────────────────────────────────────────────────────────────

function initials(name: string): string {
  return name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase();
}

function avatarColor(role: string): string {
  if (role === 'super_admin' || role === 'SUPER_ADMIN') return 'var(--accent)';
  if (role === 'IT_ADMIN')    return 'var(--blue)';
  if (role === 'EDITOR')      return 'var(--safe)';
  return 'var(--text-mid)';
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('en-IN', {
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
}

/** A check only counts as a pass when it was genuinely verified. */
function postureState(check: PostureCheck | null): 'pass' | 'fail' | 'unverified' | 'na' {
  if (!check) return 'unverified';
  if (check.status === null) return 'na';
  if (!check.verified) return 'unverified';
  return check.status ? 'pass' : 'fail';
}

function fmtAction(action: string): string {
  return action.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
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

// ── Page ───────────────────────────────────────────────────────────────────

export default function SecurityPage() {
  const [sessions, setSessions]     = useState<Session[]>([]);
  const [history, setHistory]       = useState<LoginEvent[]>([]);
  const [posture, setPosture]       = useState<Posture | null>(null);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [busyLogout, setBusyLogout] = useState<Record<string, boolean>>({});
  const [toast, setToast]           = useState<{ message: string; type: 'success' | 'error' } | null>(null);

  function showToast(message: string, type: 'success' | 'error') {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3500);
  }

  function authHeaders(): HeadersInit {
    const token = localStorage.getItem('sentinel_token');
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  async function loadSessions() {
    try {
      const res = await fetch(`${API_URL}/api/security/sessions`, { headers: authHeaders() });
      if (res.ok) {
        const data = await res.json();
        setSessions(data.sessions ?? []);
      }
    } catch { /* keep stale */ }
    finally { setLoadingSessions(false); }
  }

  async function loadHistory() {
    try {
      const res = await fetch(`${API_URL}/api/security/login-history`);
      if (res.ok) {
        const data = await res.json();
        setHistory(data.history ?? []);
      }
    } catch { /* keep stale */ }
  }

  async function loadPosture() {
    try {
      const res = await fetch(`${API_URL}/api/security/posture`);
      if (res.ok) setPosture(await res.json());
    } catch { /* keep stale */ }
  }

  useEffect(() => {
    loadSessions();
    loadHistory();
    loadPosture();
    const t = setInterval(loadSessions, 30_000);
    return () => clearInterval(t);
  }, []);

  async function forceLogout(userId: string, name: string) {
    setBusyLogout((b) => ({ ...b, [userId]: true }));
    try {
      const res = await fetch(
        `${API_URL}/api/security/sessions/${encodeURIComponent(userId)}/force-logout`,
        { method: 'POST', headers: authHeaders() }
      );
      const data = await res.json();
      if (!res.ok) { showToast(data.detail ?? 'Failed.', 'error'); return; }
      setSessions((prev) => prev.filter((s) => s.user_id !== userId));
      showToast(`${name} logged out.`, 'success');
    } catch {
      showToast('Network error.', 'error');
    } finally {
      setBusyLogout((b) => ({ ...b, [userId]: false }));
    }
  }

  // Metrics
  const totalSessions = sessions.length;
  const failedLogins  = history.filter((h) => h.status === 'BLOCKED').length;
  const mfaState        = postureState(posture?.mfa_mandatory ?? null);
  const checkStates     = POSTURE_ITEMS.map((p) => postureState(posture?.[p.key] ?? null));
  const applicableChecks = checkStates.filter((s) => s !== 'na').length;
  const passedChecks     = checkStates.filter((s) => s === 'pass').length;

  return (
    <div className="page" id="page-security">
      <div className="page-header">
        <div className="page-label">SECURITY &amp; ACCESS</div>
        <h1 className="page-title med">
          Who&apos;s in.
          <br />
          <span className="accent">Who can do what.</span>
        </h1>
        <p className="page-sub">
          Live sessions, MFA status, and security posture across the entire platform.
          Every login is logged, every privilege is auditable, every credential is
          encrypted.
        </p>
      </div>

      <div className="metrics">
        <div className="metric">
          <div className="metric-label">Active Sessions</div>
          <div className="metric-value accent">{loadingSessions ? '…' : totalSessions}</div>
          <div className="metric-delta">Logged in right now</div>
        </div>
        <div className="metric">
          <div className="metric-label">MFA Enforced</div>
          <div className={`metric-value ${mfaState === 'pass' ? 'safe' : 'critical'}`}>
            {posture ? (mfaState === 'pass' ? 'YES' : 'NO') : '…'}
          </div>
          <div className={`metric-delta ${mfaState === 'pass' ? 'safe' : 'critical'}`}>
            {mfaState === 'pass' ? 'Mandatory' : 'Not implemented'}
          </div>
        </div>
        <div className="metric">
          <div className="metric-label">Posture Checks Passed</div>
          <div className={`metric-value ${passedChecks === applicableChecks ? 'safe' : ''}`}>
            {posture ? `${passedChecks} / ${applicableChecks}` : '…'}
          </div>
          <div className="metric-delta">Verified checks only</div>
        </div>
        <div className="metric">
          <div className="metric-label">Failed Logins · 24h</div>
          <div className="metric-value">{failedLogins}</div>
          <div className={`metric-delta ${failedLogins === 0 ? 'safe' : 'critical'}`}>
            {failedLogins === 0 ? 'No brute force' : 'Blocked attempts'}
          </div>
        </div>
      </div>

      <div className="security-grid">
        {/* Active Sessions */}
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">Active Sessions</div>
            <div className="panel-tag">Logged in right now</div>
          </div>
          <div className="panel-body" style={{ padding: 0 }}>
            {loadingSessions && (
              <div className="session-row">
                <span style={{ ...MONO, fontSize: '11px', color: 'var(--dim)', padding: '16px' }}>
                  Loading…
                </span>
              </div>
            )}
            {!loadingSessions && sessions.length === 0 && (
              <div className="session-row">
                <span style={{ ...MONO, fontSize: '11px', color: 'var(--dim)', padding: '16px' }}>
                  No active sessions
                </span>
              </div>
            )}
            {sessions.map((s) => (
              <div className="session-row" key={s.user_id + s.login_at}>
                <div className="session-avatar" style={{ background: avatarColor(s.role) }}>
                  {initials(s.name)}
                </div>
                <div className="session-info">
                  <div className="name">{s.name} · {s.role.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}</div>
                  <div className="meta">
                    {s.ip} · {s.device} · {s.browser} · since {fmtTime(s.login_at)}
                  </div>
                </div>
                <div className="session-status">
                  {s.is_current
                    ? <span style={{ color: 'var(--safe)' }}>● THIS DEVICE</span>
                    : <span style={{ color: 'var(--safe)' }}>● ACTIVE</span>
                  }
                </div>
                <div className="session-action">
                  {s.is_current ? '—' : (
                    <button
                      onClick={() => forceLogout(s.user_id, s.name)}
                      disabled={busyLogout[s.user_id]}
                      style={{
                        ...MONO, fontSize: '9px', letterSpacing: '1px',
                        padding: '3px 10px', cursor: busyLogout[s.user_id] ? 'wait' : 'pointer',
                        background: 'transparent',
                        border: '1px solid rgba(255,56,56,0.35)',
                        color: 'var(--critical)',
                        opacity: busyLogout[s.user_id] ? 0.5 : 1,
                      }}
                    >
                      {busyLogout[s.user_id] ? '…' : 'FORCE LOGOUT'}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Login History */}
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">Recent Login History</div>
            <div className="panel-tag">Last 20 events · Location + Device</div>
          </div>
          <div className="panel-body" style={{ padding: '0 22px' }}>
            {history.length === 0 && (
              <div className="activity-item">
                <div className="activity-text" style={{ color: 'var(--dim)' }}>No login history yet.</div>
              </div>
            )}
            {history.map((e, i) => (
              <div className="activity-item" key={i}>
                <div className="activity-time">{fmtTime(e.timestamp)}</div>
                <div className={`activity-tag ${e.status === 'OK' ? 'safe' : 'warn'}`}>
                  {e.status}
                </div>
                <div className="activity-text">
                  {e.user} · {e.ip} · {fmtAction(e.action)}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Security Posture */}
      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Security Posture · Compliance Checklist</div>
          <div className="panel-tag">DPDP · CERT-In · IT Act</div>
        </div>
        <div className="panel-body" style={{ padding: 0 }}>
          <div className="posture-grid">
            {POSTURE_ITEMS.map((p) => {
              const check = posture?.[p.key] ?? null;
              const state = postureState(check);
              return (
                <div className="posture-cell" key={p.key}>
                  <div className={`posture-icon ${state === 'pass' ? 'pass' : 'warn'}`}>
                    {state === 'pass' ? '✓' : state === 'fail' ? '✕' : '?'}
                  </div>
                  <div className="posture-info">
                    <div className="title">
                      {p.title}
                      {state !== 'pass' && (
                        <span style={{
                          ...MONO, fontSize: '8px', letterSpacing: '1px', marginLeft: '8px',
                          padding: '1px 6px',
                          border: `1px solid ${state === 'fail' ? 'rgba(255,56,56,0.4)' : 'rgba(255,176,32,0.4)'}`,
                          color: state === 'fail' ? 'var(--critical)' : 'var(--high)',
                        }}>
                          {state === 'fail' ? 'FAIL' : state === 'na' ? 'NOT APPLICABLE' : 'NOT VERIFIED'}
                        </span>
                      )}
                    </div>
                    <div className="desc">{check?.note ?? p.desc}</div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Data Residency — reviewed against the code, not aspirational */}
      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Data Residency &amp; External Data Flows</div>
          <div className="panel-tag">Storage on-premises · these calls leave the network</div>
        </div>
        <div className="panel-body" style={{ padding: 0 }}>
          <div className="plugin-list">
            {RESIDENCY.map((r) => (
              <div className="plugin-row" key={r.name}>
                <div className="plugin-name">
                  {r.name} <span className="dim">{r.detail}</span>
                </div>
                <div style={{ ...MONO, fontSize: '11px', color: r.color, letterSpacing: '1px' }}>
                  {r.location}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {toast && <Toast message={toast.message} type={toast.type} />}
    </div>
  );
}

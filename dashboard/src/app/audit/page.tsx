'use client';

import { useEffect, useState } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

const FILTERS = ['All', 'Kaaval AI', 'Digital Kavasam', 'Veriff', 'Admin', 'Auth', 'Config', 'CERT-In'];

const MODULE_MAP: Record<string, string> = {
  'Kaaval AI': 'kaaval',
  'Digital Kavasam': 'kavasam',
  'Veriff': 'veriff',
  'Admin': 'admin',
  'Auth': 'auth',
  'Config': 'config',
  'CERT-In': 'cert_in',
};

interface AuditEvent {
  id: string;
  action: string;
  module: string;
  details_json: string | null;
  ip_address: null;
  created_at: string | null;
}

interface Metrics {
  events_24h: number;
  retention_days: number;
  retention_basis: string;
  tamper_check: string;   // PASS | PARTIAL | FAIL | NOT_ACTIVE
  tamper_detail: string;
  hashed_entries: number;
  total_entries: number;
  chain_started_at: string | null;
  chained_since_start: number;
  entries_since_start: number;
  unchained_since_start: number;
  cert_in_drafts: number;
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—';
  return new Intl.DateTimeFormat('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(iso));
}

/** Only a fully-verified chain gets the green treatment. */
function tamperClass(check: string | undefined): string {
  if (check === 'PASS') return 'safe';
  if (check === 'FAIL') return 'critical';
  return '';   // PARTIAL / NOT_ACTIVE / unknown — neutral, never green
}

function statusFor(action: string): string {
  const upper = action.toUpperCase();
  if (upper.includes('CRITICAL') || upper.includes('THREAT') || upper.includes('ATTACK')) return 'CRITICAL';
  if (upper.includes('ANOMALY') || upper.includes('FLAGGED') || upper.includes('BLOCKED')) return 'FLAGGED';
  return 'OK';
}

export default function AuditPage() {
  const [active, setActive] = useState('All');
  const [events, setEvents] = useState<AuditEvent[]>([]);
  // No optimistic defaults — an unfetched page must not assert PASS.
  const [metrics, setMetrics] = useState<Metrics | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/audit/metrics`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d) setMetrics(d); })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const params = new URLSearchParams({ limit: '50' });
    if (active !== 'All') {
      const mod = MODULE_MAP[active];
      if (mod) params.set('module', mod);
    }
    fetch(`${API_URL}/api/audit?${params}`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d) setEvents(d.events); })
      .catch(() => {});
  }, [active]);

  return (
    <div className="page" id="page-audit">
      <div className="page-header">
        <div className="page-label">AUDIT LOG</div>
        <h1 className="page-title med">
          Every action.
          <br />
          <span className="accent">Append-only. Never deleted.</span>
        </h1>
        <p className="page-sub">
          Every login, config change and agent decision is written here for DPDP
          and CERT-In compliance. A database trigger blocks UPDATE and DELETE, so
          entries cannot be altered or removed once written. Hash-chaining adds
          tamper <em>evidence</em> on top of that — the Tamper Check tile below
          reports its real coverage.
        </p>
      </div>

      <div className="metrics">
        <div className="metric">
          <div className="metric-label">Events / 24h</div>
          <div className="metric-value accent">
            {metrics ? metrics.events_24h.toLocaleString('en-IN') : '…'}
          </div>
          <div className="metric-delta">All modules</div>
        </div>
        <div className="metric">
          <div className="metric-label">Retained History</div>
          <div className="metric-value safe">{metrics ? `${metrics.retention_days}d` : '…'}</div>
          <div className="metric-delta">Oldest entry on record</div>
        </div>
        <div className="metric">
          <div className="metric-label">Tamper Check</div>
          <div className={`metric-value ${tamperClass(metrics?.tamper_check)}`}>
            {metrics?.tamper_check ?? '…'}
          </div>
          <div className="metric-delta">
            {metrics
              ? `${metrics.chained_since_start}/${metrics.entries_since_start} chained since chain start`
              : '—'}
          </div>
        </div>
        <div className="metric">
          <div className="metric-label">CERT-In Events · 90d</div>
          <div className="metric-value">{metrics ? metrics.cert_in_drafts : '…'}</div>
          <div className="metric-delta">Logged to audit trail</div>
        </div>
      </div>

      {metrics && metrics.tamper_check !== 'PASS' && (
        <div style={{
          margin: '0 0 20px', padding: '12px 16px',
          background: 'rgba(255,176,32,0.06)', border: '1px solid rgba(255,176,32,0.25)',
          fontFamily: "'Space Mono', monospace", fontSize: '11px',
          color: '#b98a2e', lineHeight: 1.6,
        }}>
          ⚠ {metrics.tamper_detail}
        </div>
      )}

      <div className="audit-filters">
        {FILTERS.map((f) => (
          <div
            key={f}
            className={`audit-filter${active === f ? ' active' : ''}`}
            onClick={() => setActive(f)}
          >
            {f}
          </div>
        ))}
        <button className="audit-export">↓ Export CSV</button>
      </div>

      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Recent Events</div>
          <div className="panel-tag">Live · append-only</div>
        </div>
        <div className="panel-body" style={{ padding: 0 }}>
          <div className="audit-log">
            {events.length === 0 ? (
              <div className="audit-row">
                <div className="audit-ts">—</div>
                <div className="audit-user">system</div>
                <div className="audit-action">No events found</div>
                <div className="audit-status">OK</div>
              </div>
            ) : (
              events.map((e) => (
                <div className="audit-row" key={e.id}>
                  <div className="audit-ts">{fmtTime(e.created_at)}</div>
                  <div className="audit-user">{e.module}</div>
                  <div className="audit-action">{e.action}</div>
                  <div className="audit-status">{statusFor(e.action)}</div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

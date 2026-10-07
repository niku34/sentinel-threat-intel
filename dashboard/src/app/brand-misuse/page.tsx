'use client';

import { useEffect, useRef, useState } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

const MONO: React.CSSProperties = { fontFamily: "'Space Mono', monospace" };

interface BrandFinding {
  finding_id: string;
  threat_type: string;
  title: string;
  description: string;
  severity: string;
  status: string;
  detected_at: string | null;
  source_url: string | null;
  confidence: number | null;
  dmca_draft_ready: boolean;
}

interface FindingsResponse {
  findings: BrandFinding[];
  total: number;
  by_type: Record<string, number>;
}

interface DmcaDraft {
  finding_id: string;
  threat_type: string;
  dmca_draft: string;
  rights_holder: string;
  filed: boolean;
  note: string;
}

type TabKey = 'ALL' | 'LOGO_MISUSE' | 'FOOTAGE_THEFT' | 'ANCHOR_IMPERSONATION';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'ALL', label: 'ALL' },
  { key: 'LOGO_MISUSE', label: 'LOGO MISUSE' },
  { key: 'FOOTAGE_THEFT', label: 'FOOTAGE THEFT' },
  { key: 'ANCHOR_IMPERSONATION', label: 'ANCHOR IMPERSONATION' },
];

const TYPE_LABELS: Record<string, string> = {
  LOGO_MISUSE: 'LOGO MISUSE',
  FOOTAGE_THEFT: 'FOOTAGE THEFT',
  ANCHOR_IMPERSONATION: 'ANCHOR IMPERSONATION',
  FAKE_PAGE: 'FAKE PAGE',
};

const TYPE_COLORS: Record<string, string> = {
  LOGO_MISUSE: 'var(--accent)',
  FOOTAGE_THEFT: 'var(--blue)',
  ANCHOR_IMPERSONATION: 'var(--high)',
  FAKE_PAGE: 'var(--medium)',
};

const SEV_CLASS: Record<string, string> = {
  CRITICAL: 'critical',
  HIGH: 'high',
  MEDIUM: 'medium',
  LOW: 'low',
};

function fmtTs(iso: string | null): string {
  if (!iso) return '—';
  return new Intl.DateTimeFormat('en-IN', {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(iso));
}

function DmcaDraftModal({ draft, onClose }: { draft: DmcaDraft; onClose: () => void }) {
  const overlayRef = useRef<HTMLDivElement>(null);

  function handleOverlayClick(e: React.MouseEvent) {
    if (e.target === overlayRef.current) onClose();
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') onClose(); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      ref={overlayRef}
      onClick={handleOverlayClick}
      style={{
        position: 'fixed', inset: 0, zIndex: 50,
        background: 'rgba(0,0,0,0.9)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '24px', overflowY: 'auto',
      }}
    >
      <div style={{
        background: '#0a0a0a', border: '1px solid #1f1f1f',
        width: '100%', maxWidth: '640px', maxHeight: '90vh',
        overflowY: 'auto', position: 'relative', padding: '40px',
      }}>
        <button
          onClick={onClose}
          style={{
            position: 'absolute', top: '16px', right: '16px',
            background: 'none', border: 'none', color: '#555',
            fontSize: '20px', cursor: 'pointer', lineHeight: 1, padding: '4px 8px',
          }}
        >
          ✕
        </button>

        <div style={{ marginBottom: '24px' }}>
          <div style={{ ...MONO, fontSize: '9px', color: '#444', letterSpacing: '3px', textTransform: 'uppercase', marginBottom: '8px' }}>
            DMCA TAKEDOWN DRAFT
          </div>
          <div style={{ ...MONO, fontSize: '16px', color: 'var(--accent)', letterSpacing: '1px' }}>
            {TYPE_LABELS[draft.threat_type] ?? draft.threat_type}
          </div>
          <div style={{ width: '40px', height: '1px', background: 'var(--accent)', marginTop: '12px' }} />
        </div>

        <pre style={{
          ...MONO, fontSize: '12px', color: '#d0d0d0', lineHeight: 1.6,
          whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          background: 'var(--bg)', borderLeft: '2px solid var(--accent)',
          padding: '16px 18px', margin: 0,
        }}>
          {draft.dmca_draft}
        </pre>

        <div style={{
          marginTop: '20px', padding: '12px 16px',
          background: 'rgba(255,91,58,0.08)', border: '1px solid rgba(255,91,58,0.3)',
          ...MONO, fontSize: '10px', color: 'var(--accent)', letterSpacing: '0.5px', lineHeight: 1.5,
        }}>
          ⚠ {draft.note} Sentinel never files on your behalf.
        </div>
      </div>
    </div>
  );
}

export default function BrandMisusePage() {
  const [data, setData] = useState<FindingsResponse | null>(null);
  const [tab, setTab] = useState<TabKey>('ALL');
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [draft, setDraft] = useState<DmcaDraft | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    fetch(`${API_URL}/api/brand-misuse/findings`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d) setData(d); })
      .catch(() => setError('Failed to load findings.'));
  }

  useEffect(() => { load(); }, []);

  function confirmFinding(id: string) {
    setBusy((p) => ({ ...p, [id]: true }));
    fetch(`${API_URL}/api/brand-misuse/${id}/confirm`, { method: 'POST' })
      .then((r) => r.ok ? r.json() : null)
      .then((res) => {
        if (!res) return;
        // Optimistically update the card in place.
        setData((prev) => prev && {
          ...prev,
          findings: prev.findings.map((f) =>
            f.finding_id === id
              ? { ...f, status: 'confirmed', dmca_draft_ready: true }
              : f,
          ),
        });
      })
      .catch(() => setError('Confirm failed.'))
      .finally(() => setBusy((p) => ({ ...p, [id]: false })));
  }

  function viewDraft(id: string) {
    fetch(`${API_URL}/api/brand-misuse/${id}/dmca-draft`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d) setDraft(d); })
      .catch(() => setError('Could not load DMCA draft.'));
  }

  const findings = (data?.findings ?? []).filter(
    (f) => tab === 'ALL' || f.threat_type === tab,
  );

  return (
    <div className="page" id="page-brand-misuse">
      <div className="page-header">
        <div className="page-label">BRAND MISUSE</div>
        <h1 className="page-title">
          Logo misuse, footage theft &amp; anchor impersonation.
          <br />
          <span className="accent">கவலை வேண்டாம் — we watch the brand.</span>
        </h1>
        <p className="page-sub">
          A unified view of unauthorized use of Example Broadcaster logos, archival footage, and
          anchor likeness across platforms. Every finding is reviewed by editorial before
          any action — the platform detects and drafts, humans decide and file.
        </p>
      </div>

      {/* Human-in-the-loop warning banner */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: '12px',
        padding: '14px 18px', marginBottom: '28px',
        background: 'rgba(255,91,58,0.08)', border: '1px solid rgba(255,91,58,0.3)',
      }}>
        <span style={{ fontSize: '16px' }}>⚠</span>
        <span style={{ ...MONO, fontSize: '11px', color: 'var(--accent)', letterSpacing: '0.5px', lineHeight: 1.5 }}>
          All actions require editorial approval. Platform never auto-files or
          auto-takes-down. DMCA notices are drafted for human review only.
        </span>
      </div>

      {error && (
        <div style={{ ...MONO, fontSize: '11px', color: 'var(--critical)', marginBottom: '16px' }}>
          {error}
        </div>
      )}

      {/* Filter tabs */}
      <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', marginBottom: '28px' }}>
        {TABS.map((t) => {
          const isActive = tab === t.key;
          const count = t.key === 'ALL'
            ? (data?.total ?? 0)
            : (data?.by_type?.[t.key] ?? 0);
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              style={{
                ...MONO, fontSize: '10px', letterSpacing: '1px', cursor: 'pointer',
                padding: '8px 14px',
                background: isActive ? 'rgba(255,91,58,0.12)' : 'transparent',
                border: `1px solid ${isActive ? 'rgba(255,91,58,0.4)' : 'var(--line)'}`,
                color: isActive ? 'var(--accent)' : 'var(--text-mid)',
              }}
            >
              {t.label} <span style={{ color: '#555' }}>· {count}</span>
            </button>
          );
        })}
      </div>

      {/* Finding cards */}
      {findings.length === 0 ? (
        <div className="finding">
          <div className="finding-body" style={{ color: 'var(--dim)' }}>
            {data ? 'No findings in this category.' : 'Loading findings…'}
          </div>
        </div>
      ) : (
        findings.map((f) => {
          const isDetected = f.status === 'detected';
          const confPct = f.confidence != null ? Math.round(f.confidence * 100) : null;
          return (
            <div className="finding" key={f.finding_id}>
              <div className="finding-head">
                <div>
                  <div className="finding-id" style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                    <span style={{ color: TYPE_COLORS[f.threat_type] ?? 'var(--accent)' }}>
                      {TYPE_LABELS[f.threat_type] ?? f.threat_type}
                    </span>
                  </div>
                  <div className="finding-title">{f.title}</div>
                </div>
                <div className={`finding-sev-badge ${SEV_CLASS[f.severity] ?? ''}`}>
                  {f.severity}
                </div>
              </div>

              <div className="finding-meta">
                {f.source_url && (
                  <div className="finding-meta-item">
                    <span className="label">Source</span>{' '}
                    <a
                      href={f.source_url}
                      target="_blank"
                      rel="noreferrer"
                      style={{ color: 'var(--blue)', textDecoration: 'none', wordBreak: 'break-all' }}
                    >
                      {f.source_url}
                    </a>
                  </div>
                )}
                {confPct != null && (
                  <div className="finding-meta-item">
                    <span className="label">Confidence</span>{' '}
                    <span style={{ color: 'var(--accent)' }}>{confPct}%</span>
                  </div>
                )}
                <div className="finding-meta-item">
                  <span className="label">Detected</span> {fmtTs(f.detected_at)}
                </div>
                <div className="finding-meta-item">
                  <span className="label">Status</span>{' '}
                  <span style={{
                    ...MONO,
                    color: f.status === 'confirmed' ? 'var(--safe)' : 'var(--medium)',
                    letterSpacing: '1px', textTransform: 'uppercase',
                  }}>
                    {f.status}
                  </span>
                </div>
              </div>

              <div className="finding-body">{f.description}</div>

              {/* Human-in-the-loop actions */}
              <div style={{
                marginTop: '14px', paddingTop: '14px', borderTop: '1px solid var(--line)',
                display: 'flex', gap: '12px', flexWrap: 'wrap', alignItems: 'center',
              }}>
                {isDetected && (
                  <button
                    onClick={() => confirmFinding(f.finding_id)}
                    disabled={busy[f.finding_id]}
                    style={{
                      ...MONO, fontSize: '11px', letterSpacing: '1px',
                      cursor: busy[f.finding_id] ? 'wait' : 'pointer',
                      padding: '8px 16px',
                      background: 'rgba(74,222,128,0.1)',
                      border: '1px solid rgba(74,222,128,0.35)',
                      color: 'var(--safe)',
                      opacity: busy[f.finding_id] ? 0.6 : 1,
                    }}
                  >
                    {busy[f.finding_id] ? 'CONFIRMING…' : '✓ CONFIRM FINDING'}
                  </button>
                )}

                {f.dmca_draft_ready && (
                  <button
                    onClick={() => viewDraft(f.finding_id)}
                    style={{
                      ...MONO, fontSize: '11px', letterSpacing: '1px', cursor: 'pointer',
                      padding: '8px 16px',
                      background: 'rgba(255,91,58,0.1)',
                      border: '1px solid rgba(255,91,58,0.35)',
                      color: 'var(--accent)',
                    }}
                  >
                    VIEW DMCA DRAFT
                  </button>
                )}

                {!isDetected && !f.dmca_draft_ready && (
                  <span style={{ ...MONO, fontSize: '10px', color: 'var(--text-dim)', letterSpacing: '1px' }}>
                    {f.status.toUpperCase()} — no action pending
                  </span>
                )}
              </div>
            </div>
          );
        })
      )}

      {draft && <DmcaDraftModal draft={draft} onClose={() => setDraft(null)} />}
    </div>
  );
}

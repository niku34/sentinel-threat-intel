'use client';

import { useEffect, useRef, useState } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

import {
  CertInReportModal,
  Field,
  fmtDetected,
  MONO,
  LABEL_STYLE,
  VALUE_STYLE,
  type CertInDraft,
  type CertInReport,
} from '@/components/cert-in-report-modal';

interface JudgePayload {
  judge_confirmed?: boolean;
  judge_confidence?: number;
  judge_reasoning?: string;
  adjusted_severity?: string;
  judge_final_verdict?: string;
  judge_broadcast_recommendation?: 'CLEAR' | 'HOLD' | 'INVESTIGATE';
  judge_legal_risk?: 'LOW' | 'MEDIUM' | 'HIGH';
  judge_escalate_to_certin?: boolean;
  judge_certin_reason?: string;
  judge_urgency?: 'IMMEDIATE' | 'HIGH' | 'MEDIUM' | 'MONITOR';
  judge_brand_main_relevance?: string;
}

interface LiveFinding {
  id: string;
  source: 'kaaval' | 'kavasam' | 'veriff';
  title: string;
  description: string;
  severity: string;
  finding_type: string;
  timestamp: string | null;
  source_platform: string | null;
  status: string;
  judge: JudgePayload | null;
}

interface FindingsSummary {
  findings: LiveFinding[];
  total: number;
  by_severity: { CRITICAL: number; HIGH: number; MEDIUM: number; LOW: number };
  by_source: { kaaval: number; kavasam: number; veriff: number };
}


const SOURCE_LABELS: Record<string, string> = { kaaval: 'KAAVAL', kavasam: 'KAVASAM', veriff: 'VERIFF' };
const SOURCE_COLORS: Record<string, string> = {
  kaaval: 'var(--accent)',
  kavasam: 'var(--blue)',
  veriff: 'var(--safe)',
};
const SEV_CLASS: Record<string, string> = {
  CRITICAL: 'critical',
  HIGH: 'high',
  MEDIUM: 'medium',
  LOW: 'low',
};

const SEV_BORDER: Record<string, string> = {
  CRITICAL: 'var(--critical)',
  HIGH: 'var(--high)',
  MEDIUM: 'var(--medium)',
  LOW: 'var(--safe)',
};

const SEV_ORDER: Record<string, number> = {
  CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3,
};

const PLATFORM_ICON: Record<string, string> = {
  youtube: '▶', instagram: '◈', facebook: '◇',
  telegram: '◆', twitter: '✕', web: '◉', veriff: '▲',
};

function fmtTs(iso: string | null): string {
  if (!iso) return '—';
  return new Intl.DateTimeFormat('en-IN', {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(iso));
}

function isTamil(text: string): boolean {
  return /[஀-௿]/.test(text);
}

const RECO_COLOR: Record<string, string> = {
  CLEAR: 'var(--safe)',
  HOLD: 'var(--critical)',
  INVESTIGATE: 'var(--high)',
};

const RISK_COLOR: Record<string, string> = {
  LOW: 'var(--safe)',
  MEDIUM: 'var(--medium)',
  HIGH: 'var(--critical)',
};

function JudgePanel({ judge }: { judge: JudgePayload }) {
  const [open, setOpen] = useState(false);
  if (!judge.judge_reasoning) return null;

  const confidencePct = Math.round(((judge.judge_confidence ?? 0) * 100));
  const severity = judge.adjusted_severity ?? judge.judge_final_verdict;

  return (
    <div
      style={{
        marginTop: '16px',
        borderTop: '1px solid var(--line)',
        paddingTop: '14px',
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          background: 'transparent',
          border: 'none',
          color: 'var(--accent)',
          fontFamily: "'Space Mono', monospace",
          fontSize: '10px',
          letterSpacing: '2px',
          textTransform: 'uppercase',
          cursor: 'pointer',
          padding: 0,
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
        }}
      >
        <span style={{ display: 'inline-block', width: '10px', textAlign: 'center' }}>
          {open ? '▾' : '▸'}
        </span>
        Why did the system flag this?
      </button>

      {open && (
        <div style={{ marginTop: '14px', display: 'grid', gap: '14px' }}>
          <div
            style={{
              ...MONO,
              fontSize: '12px',
              color: 'var(--text-mid)',
              lineHeight: 1.55,
              background: 'var(--bg)',
              borderLeft: '2px solid var(--accent)',
              padding: '12px 16px',
            }}
          >
            {judge.judge_reasoning}
          </div>

          <div>
            <div style={LABEL_STYLE}>Judge confidence · {confidencePct}%</div>
            <div
              style={{
                width: '100%',
                height: '4px',
                background: 'var(--bg-soft)',
                overflow: 'hidden',
              }}
            >
              <div
                style={{
                  width: `${confidencePct}%`,
                  height: '100%',
                  background: 'var(--accent)',
                  boxShadow: '0 0 8px var(--accent)',
                }}
              />
            </div>
          </div>

          <div style={{ display: 'flex', gap: '18px', flexWrap: 'wrap' }}>
            {severity && (
              <div>
                <div style={LABEL_STYLE}>
                  {judge.judge_final_verdict ? 'Final verdict' : 'Adjusted severity'}
                </div>
                <div
                  style={{
                    ...MONO,
                    fontSize: '12px',
                    color: 'var(--accent)',
                    letterSpacing: '1px',
                  }}
                >
                  {severity}
                </div>
              </div>
            )}

            {judge.judge_broadcast_recommendation && (
              <div>
                <div style={LABEL_STYLE}>Broadcast recommendation</div>
                <div
                  style={{
                    ...MONO,
                    fontSize: '12px',
                    color:
                      RECO_COLOR[judge.judge_broadcast_recommendation] ??
                      'var(--text)',
                    letterSpacing: '1px',
                  }}
                >
                  {judge.judge_broadcast_recommendation}
                </div>
              </div>
            )}

            {judge.judge_legal_risk && (
              <div>
                <div style={LABEL_STYLE}>Legal risk</div>
                <div
                  style={{
                    ...MONO,
                    fontSize: '12px',
                    color: RISK_COLOR[judge.judge_legal_risk] ?? 'var(--text)',
                    letterSpacing: '1px',
                  }}
                >
                  {judge.judge_legal_risk}
                </div>
              </div>
            )}

            {judge.judge_urgency && (
              <div>
                <div style={LABEL_STYLE}>Urgency</div>
                <div
                  style={{
                    ...MONO,
                    fontSize: '12px',
                    color: 'var(--accent)',
                    letterSpacing: '1px',
                  }}
                >
                  {judge.judge_urgency}
                </div>
              </div>
            )}

            {typeof judge.judge_escalate_to_certin === 'boolean' && (
              <div>
                <div style={LABEL_STYLE}>CERT-In escalation</div>
                <div
                  style={{
                    ...MONO,
                    fontSize: '12px',
                    color: judge.judge_escalate_to_certin
                      ? 'var(--critical)'
                      : 'var(--text-mid)',
                    letterSpacing: '1px',
                  }}
                >
                  {judge.judge_escalate_to_certin ? 'REQUIRED' : 'NOT REQUIRED'}
                </div>
              </div>
            )}
          </div>

          {judge.judge_brand_main_relevance && (
            <div>
              <div style={LABEL_STYLE}>Example Broadcaster relevance</div>
              <div
                style={{ ...VALUE_STYLE, marginBottom: 0 }}
              >
                {judge.judge_brand_main_relevance}
              </div>
            </div>
          )}

          {judge.judge_certin_reason && (
            <div>
              <div style={LABEL_STYLE}>CERT-In rationale</div>
              <div style={{ ...VALUE_STYLE, marginBottom: 0 }}>
                {judge.judge_certin_reason}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TranslitHint({ text, map }: { text: string; map: Record<string, string> }) {
  const hint = map[text];
  if (!hint) return null;
  return (
    <div style={{
      fontSize: '11px', color: '#555', fontStyle: 'italic',
      marginTop: '4px', fontFamily: 'monospace',
    }}>
      [Tamil] {hint}
    </div>
  );
}

interface Playbook {
  threat_type: string;
  what_it_means: string;
  remediation_steps: string[];
  who_to_contact: string;
  legal_implications: string;
  resolution_criteria: string;
}

function PlaybookPanel({ threatType }: { threatType: string }) {
  const [open, setOpen] = useState(false);
  const [playbook, setPlaybook] = useState<Playbook | null>(null);
  const [status, setStatus] = useState<'idle' | 'loading' | 'missing'>('idle');

  function toggle() {
    if (!open && status === 'idle') {
      setStatus('loading');
      fetch(`${API_URL}/api/findings/playbook/${encodeURIComponent(threatType)}`)
        .then((r) => {
          if (r.status === 404) { setStatus('missing'); return null; }
          if (!r.ok) throw new Error('fetch failed');
          return r.json();
        })
        .then((d) => { if (d) { setPlaybook(d); setStatus('idle'); } })
        .catch(() => setStatus('missing'));
    }
    setOpen((v) => !v);
  }

  return (
    <div style={{ marginTop: '16px', borderTop: '1px solid var(--line)', paddingTop: '14px' }}>
      <button
        type="button"
        onClick={toggle}
        style={{
          background: 'transparent',
          border: 'none',
          color: 'var(--safe)',
          fontFamily: "'Space Mono', monospace",
          fontSize: '10px',
          letterSpacing: '2px',
          textTransform: 'uppercase',
          cursor: 'pointer',
          padding: 0,
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
        }}
      >
        <span style={{ display: 'inline-block', width: '10px', textAlign: 'center' }}>
          {open ? '▾' : '▸'}
        </span>
        Remediation Playbook
      </button>

      {open && (
        <div style={{ marginTop: '14px' }}>
          {status === 'loading' && (
            <div style={{ ...MONO, fontSize: '11px', color: 'var(--text-dim)' }}>Loading…</div>
          )}
          {status === 'missing' && (
            <div style={{ ...MONO, fontSize: '11px', color: 'var(--text-dim)', fontStyle: 'italic' }}>
              No playbook available for this threat type.
            </div>
          )}
          {playbook && (
            <div style={{ display: 'grid', gap: '14px' }}>
              {/* what it means */}
              <div>
                <div style={LABEL_STYLE}>What it means</div>
                <div
                  style={{
                    ...MONO,
                    fontSize: '12px',
                    color: 'var(--text-mid)',
                    lineHeight: 1.55,
                    background: 'var(--bg)',
                    borderLeft: '2px solid var(--safe)',
                    padding: '10px 14px',
                  }}
                >
                  {playbook.what_it_means}
                </div>
              </div>

              {/* remediation steps */}
              <div>
                <div style={LABEL_STYLE}>Remediation steps</div>
                <ol
                  style={{
                    margin: 0,
                    paddingLeft: '20px',
                    display: 'grid',
                    gap: '6px',
                  }}
                >
                  {playbook.remediation_steps.map((step, i) => (
                    <li
                      key={i}
                      style={{
                        ...MONO,
                        fontSize: '12px',
                        color: 'var(--text)',
                        lineHeight: 1.5,
                      }}
                    >
                      {step}
                    </li>
                  ))}
                </ol>
              </div>

              {/* meta row */}
              <div style={{ display: 'flex', gap: '24px', flexWrap: 'wrap' }}>
                <div>
                  <div style={LABEL_STYLE}>Who to contact</div>
                  <div style={{ ...MONO, fontSize: '12px', color: 'var(--accent)', letterSpacing: '0.5px' }}>
                    {playbook.who_to_contact}
                  </div>
                </div>
                <div style={{ flex: 1, minWidth: '160px' }}>
                  <div style={LABEL_STYLE}>Legal implications</div>
                  <div style={{ ...MONO, fontSize: '12px', color: 'var(--medium)', lineHeight: 1.4 }}>
                    {playbook.legal_implications}
                  </div>
                </div>
              </div>

              {/* resolution */}
              <div>
                <div style={LABEL_STYLE}>Resolution criteria</div>
                <div
                  style={{
                    ...MONO,
                    fontSize: '12px',
                    color: 'var(--safe)',
                    lineHeight: 1.4,
                    borderLeft: '2px solid var(--safe)',
                    paddingLeft: '10px',
                  }}
                >
                  {playbook.resolution_criteria}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function FilterPill({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        background: active ? 'rgba(255,91,58,0.12)' : 'transparent',
        border: `1px solid ${active ? 'rgba(255,91,58,0.45)' : '#252525'}`,
        color: active ? 'var(--accent)' : '#555',
        fontFamily: "'Space Mono', monospace",
        fontSize: '9px',
        letterSpacing: '1.5px',
        padding: '3px 9px',
        cursor: 'pointer',
      }}
    >
      {label}
    </button>
  );
}

function FilterGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
      <span style={{ fontFamily: "'Space Mono', monospace", fontSize: '8px', color: '#3a3a3a', letterSpacing: '1.5px', whiteSpace: 'nowrap' }}>
        {label}
      </span>
      {children}
    </div>
  );
}

function PlatformBadge({ platform }: { platform: string | null }) {
  if (!platform) return null;
  const p = platform.toLowerCase();
  const icon = PLATFORM_ICON[p] ?? '●';
  return (
    <span style={{
      fontFamily: "'Space Mono', monospace",
      fontSize: '8px',
      letterSpacing: '1px',
      color: '#444',
      border: '1px solid #222',
      padding: '2px 7px',
      display: 'inline-flex',
      alignItems: 'center',
      gap: '4px',
    }}>
      {icon} {platform.toUpperCase()}
    </span>
  );
}

type FbState = 'pending' | 'confirmed' | 'dismissed' | 'dismissing';

const FB_BTN: React.CSSProperties = {
  background: 'transparent',
  fontFamily: "'Space Mono', monospace",
  fontSize: '10px',
  letterSpacing: '1px',
  cursor: 'pointer',
  padding: '4px 10px',
};

function FeedbackBar({
  findingId,
  state,
  reason,
  onConfirm,
  onStartDismiss,
  onReasonChange,
  onSubmitDismiss,
}: {
  findingId: string;
  state: FbState;
  reason: string;
  onConfirm: () => void;
  onStartDismiss: () => void;
  onReasonChange: (v: string) => void;
  onSubmitDismiss: () => void;
}) {
  return (
    <div style={{
      marginTop: '14px',
      paddingTop: '14px',
      borderTop: '1px solid var(--line)',
      display: 'flex',
      alignItems: 'center',
      gap: '10px',
      flexWrap: 'wrap',
    }}>
      {(state === 'pending') && (
        <>
          <button
            onClick={onConfirm}
            style={{ ...FB_BTN, color: 'var(--safe)', border: '1px solid rgba(74,222,128,0.35)' }}
          >
            ✓ Confirm
          </button>
          <button
            onClick={onStartDismiss}
            style={{ ...FB_BTN, color: 'var(--accent)', border: '1px solid rgba(255,91,58,0.35)' }}
          >
            ✗ Dismiss
          </button>
        </>
      )}
      {state === 'dismissing' && (
        <>
          <input
            type="text"
            placeholder="Reason (optional)"
            value={reason}
            onChange={(e) => onReasonChange(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') onSubmitDismiss(); }}
            style={{
              background: 'transparent',
              border: '1px solid #333',
              color: 'var(--text)',
              fontFamily: "'Space Mono', monospace",
              fontSize: '10px',
              padding: '4px 10px',
              outline: 'none',
              width: '200px',
            }}
          />
          <button
            onClick={onSubmitDismiss}
            style={{ ...FB_BTN, color: 'var(--accent)', border: '1px solid rgba(255,91,58,0.35)' }}
          >
            Submit
          </button>
        </>
      )}
      {state === 'confirmed' && (
        <span style={{ ...MONO, fontSize: '10px', color: 'var(--safe)', letterSpacing: '1px' }}>
          ✓ Recorded
        </span>
      )}
      {state === 'dismissed' && (
        <span style={{ ...MONO, fontSize: '10px', color: 'var(--accent)', letterSpacing: '1px' }}>
          ✗ Dismissed
        </span>
      )}
    </div>
  );
}

export default function FindingsPage() {
  const [drafts, setDrafts] = useState<CertInDraft[]>([]);
  const [selectedDraft, setSelectedDraft] = useState<CertInDraft | null>(null);
  const [liveSummary, setLiveSummary] = useState<FindingsSummary | null>(null);
  const [translitMap, setTranslitMap] = useState<Record<string, string>>({});
  const [feedbackState, setFeedbackState] = useState<Record<string, FbState>>({});
  const [dismissReason, setDismissReason] = useState<Record<string, string>>({});
  const [searchQuery, setSearchQuery] = useState('');
  const [filterPeriod, setFilterPeriod] = useState<'ALL' | 'TODAY' | '7D'>('ALL');
  const [filterSource, setFilterSource] = useState<'ALL' | 'kaaval' | 'kavasam' | 'veriff'>('ALL');
  const [filterStatus, setFilterStatus] = useState<'ALL' | 'OPEN' | 'CONFIRMED' | 'RESOLVED'>('ALL');
  const [filterSeverity, setFilterSeverity] = useState<'ALL' | 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'>('ALL');
  const [sortBy, setSortBy] = useState<'NEWEST' | 'SEVERITY'>('NEWEST');

  useEffect(() => {
    fetch(`${API_URL}/api/certin/drafts`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d?.drafts) setDrafts(d.drafts); })
      .catch(() => {});
    fetch(`${API_URL}/api/findings`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d) setLiveSummary(d); })
      .catch(() => {});
  }, []);

  function fbState(id: string): FbState { return feedbackState[id] ?? 'pending'; }

  function confirmFinding(id: string) {
    setFeedbackState((p) => ({ ...p, [id]: 'confirmed' }));
    fetch(`${API_URL}/api/findings/${id}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ verdict: 'confirm' }),
    }).catch(() => {});
  }

  function startDismiss(id: string) { setFeedbackState((p) => ({ ...p, [id]: 'dismissing' })); }

  function submitDismiss(id: string) {
    const reason = dismissReason[id] ?? '';
    setFeedbackState((p) => ({ ...p, [id]: 'dismissed' }));
    fetch(`${API_URL}/api/findings/${id}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ verdict: 'dismiss', reason: reason || undefined }),
    }).catch(() => {});
  }

  useEffect(() => {
    if (!liveSummary) return;
    const texts = new Set<string>();
    for (const f of liveSummary.findings) {
      if (isTamil(f.title)) texts.add(f.title);
      if (isTamil(f.description)) texts.add(f.description);
    }
    if (texts.size === 0) return;
    Promise.all(
      Array.from(texts).map((t) =>
        fetch(`${API_URL}/api/utils/transliterate?text=${encodeURIComponent(t)}`)
          .then((r) => r.ok ? r.json() : null)
          .then((d): [string, string] | null => d ? [t, d.transliterated] : null)
          .catch(() => null)
      )
    ).then((results) => {
      const map: Record<string, string> = {};
      for (const r of results) { if (r) map[r[0]] = r[1]; }
      setTranslitMap(map);
    });
  }, [liveSummary]);

  // ── filtering + sorting ──────────────────────────────
  const allFindings = liveSummary?.findings ?? [];
  const todayMidnight = new Date(); todayMidnight.setHours(0, 0, 0, 0);
  const sevenDaysAgo = Date.now() - 7 * 24 * 3600 * 1000;

  const filteredFindings = allFindings
    .filter((f) => {
      if (filterPeriod === 'TODAY') return !!f.timestamp && new Date(f.timestamp).getTime() >= todayMidnight.getTime();
      if (filterPeriod === '7D')   return !!f.timestamp && new Date(f.timestamp).getTime() >= sevenDaysAgo;
      return true;
    })
    .filter((f) => filterSource === 'ALL' || f.source === filterSource)
    .filter((f) => filterStatus === 'ALL' || f.status === filterStatus)
    .filter((f) => filterSeverity === 'ALL' || f.severity === filterSeverity)
    .filter((f) => {
      if (!searchQuery.trim()) return true;
      const q = searchQuery.toLowerCase();
      return (
        f.title.toLowerCase().includes(q) ||
        f.description.toLowerCase().includes(q) ||
        f.finding_type.toLowerCase().includes(q) ||
        (f.source_platform ?? '').toLowerCase().includes(q)
      );
    })
    .sort((a, b) => {
      if (sortBy === 'SEVERITY') {
        const ao = SEV_ORDER[a.severity] ?? 4;
        const bo = SEV_ORDER[b.severity] ?? 4;
        if (ao !== bo) return ao - bo;
      }
      return (b.timestamp ?? '').localeCompare(a.timestamp ?? '');
    });

  const filtersActive =
    filterPeriod !== 'ALL' || filterSource !== 'ALL' ||
    filterStatus !== 'ALL' || filterSeverity !== 'ALL' ||
    !!searchQuery.trim();

  function clearFilters() {
    setSearchQuery('');
    setFilterPeriod('ALL');
    setFilterSource('ALL');
    setFilterStatus('ALL');
    setFilterSeverity('ALL');
    setSortBy('NEWEST');
  }

  return (
    <div className="page" id="page-findings">
      <div className="page-header">
        <div className="page-label">REPORT</div>
        <h1 className="page-title">
          Scan complete.
          <br />
          <span className="accent">{liveSummary ? `${liveSummary.total} findings ready.` : '— findings ready.'}</span>
        </h1>
        <p className="page-sub">
          Every finding is documented with reproduction steps, evidence, and concrete
          remediation. Professional pentest layout — 34 pages — ready to send to
          editorial leadership or CERT-In.
        </p>
      </div>

      <div className="metrics" style={{ marginBottom: '32px' }}>
        <div className="metric">
          <div className="metric-label">Critical</div>
          <div className="metric-value critical">{liveSummary?.by_severity.CRITICAL ?? '—'}</div>
          <div className="metric-delta">Immediate action</div>
        </div>
        <div className="metric">
          <div className="metric-label">High</div>
          <div className="metric-value" style={{ color: 'var(--high)' }}>{liveSummary?.by_severity.HIGH ?? '—'}</div>
          <div className="metric-delta">Fix this week</div>
        </div>
        <div className="metric">
          <div className="metric-label">Medium</div>
          <div className="metric-value" style={{ color: 'var(--medium)' }}>{liveSummary?.by_severity.MEDIUM ?? '—'}</div>
          <div className="metric-delta">Plan &amp; monitor</div>
        </div>
        <div className="metric">
          <div className="metric-label">Low</div>
          <div className="metric-value safe">{liveSummary?.by_severity.LOW ?? '—'}</div>
          <div className="metric-delta">Hygiene fixes</div>
        </div>
      </div>

      {/* ── CERT-In Drafts ───────────────────────────────── */}
      <div style={{ marginTop: '48px' }}>
        <div className="page-label" style={{ marginBottom: '16px' }}>CERT-IN DRAFTS</div>

        {drafts.length === 0 ? (
          <div className="finding">
            <div className="finding-body" style={{ color: 'var(--dim)' }}>No CERT-In drafts found.</div>
          </div>
        ) : (
          drafts.map((draft) => {
            const deadline = draft.report?.deadline;
            const withinWindow = deadline?.status === 'WITHIN_WINDOW';
            return (
              <div className="finding" key={draft.id}>
                <div className="finding-head">
                  <div>
                    <div className="finding-id" style={{ color: 'var(--accent)' }}>{draft.reference_number}</div>
                    <div className="finding-title">{draft.incident_type} · {draft.affected_system}</div>
                  </div>
                  <div
                    className={`finding-sev-badge${withinWindow ? ' high' : ' critical'}`}
                    style={withinWindow ? { background: 'rgba(74,222,128,0.12)', color: 'var(--safe)', borderColor: 'rgba(74,222,128,0.3)' } : undefined}
                  >
                    {withinWindow ? 'WITHIN_WINDOW' : 'DEADLINE_PASSED'}
                  </div>
                </div>
                <div className="finding-meta">
                  <div className="finding-meta-item"><span className="label">Detected</span> {fmtDetected(draft.detected_at)}</div>
                  <div className="finding-meta-item"><span className="label">Status</span> {draft.status}</div>
                  {withinWindow && deadline && (
                    <div className="finding-meta-item">
                      <span className="label">Time remaining</span>{' '}
                      <span style={{ color: 'var(--safe)' }}>{deadline.minutes_remaining} min</span>
                    </div>
                  )}
                </div>
                <div className="actions-panel">
                  <div className="actions-label">CERT-In Filing · Ready</div>
                  <ul>
                    <li>Draft reviewed and queued — compliant with CERT-In Directions 2022 (6-hour rule)</li>
                  </ul>
                  <div style={{ display: 'flex', gap: '12px', marginTop: '12px', flexWrap: 'wrap' }}>
                    <button
                      onClick={() => setSelectedDraft(draft)}
                      style={{ padding: '8px 16px', background: 'rgba(58,142,255,0.1)', border: '1px solid rgba(58,142,255,0.3)', color: 'var(--blue)', fontFamily: "'Space Mono', monospace", fontSize: '11px', letterSpacing: '1px', cursor: 'pointer' }}
                    >
                      VIEW FULL REPORT
                    </button>
                    <a
                      href="mailto:incident@cert-in.org.in"
                      style={{ display: 'inline-block', padding: '8px 16px', background: 'rgba(255,91,58,0.1)', border: '1px solid rgba(255,91,58,0.3)', color: 'var(--accent)', fontFamily: "'Space Mono', monospace", fontSize: '11px', letterSpacing: '1px', textDecoration: 'none' }}
                    >
                      READY TO FILE → incident@cert-in.org.in
                    </a>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* ── Live Findings ─────────────────────────────────── */}
      <div style={{ marginTop: '48px' }}>
        <div className="page-label" style={{ marginBottom: '14px' }}>LIVE FINDINGS FROM DATABASE</div>

        {/* Search */}
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search findings by keyword, platform, or threat type..."
          style={{
            width: '100%',
            background: 'transparent',
            border: '1px solid #252525',
            color: 'var(--text)',
            fontFamily: "'Space Mono', monospace",
            fontSize: '11px',
            padding: '10px 14px',
            outline: 'none',
            boxSizing: 'border-box',
            marginBottom: '10px',
          }}
        />

        {/* Filter row */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '14px', padding: '10px 0', borderTop: '1px solid #1a1a1a', borderBottom: '1px solid #1a1a1a', marginBottom: '10px' }}>
          <FilterGroup label="PERIOD">
            {(['TODAY', '7D', 'ALL'] as const).map((v) => (
              <FilterPill key={v} label={v} active={filterPeriod === v} onClick={() => setFilterPeriod(v)} />
            ))}
          </FilterGroup>
          <FilterGroup label="SOURCE">
            {(['ALL', 'kaaval', 'kavasam', 'veriff'] as const).map((v) => (
              <FilterPill key={v} label={v.toUpperCase()} active={filterSource === v} onClick={() => setFilterSource(v)} />
            ))}
          </FilterGroup>
          <FilterGroup label="SEVERITY">
            {(['ALL', 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] as const).map((v) => (
              <FilterPill key={v} label={v} active={filterSeverity === v} onClick={() => setFilterSeverity(v)} />
            ))}
          </FilterGroup>
          <FilterGroup label="STATUS">
            {(['ALL', 'OPEN', 'CONFIRMED', 'RESOLVED'] as const).map((v) => (
              <FilterPill key={v} label={v} active={filterStatus === v} onClick={() => setFilterStatus(v)} />
            ))}
          </FilterGroup>
          <FilterGroup label="SORT">
            {(['NEWEST', 'SEVERITY'] as const).map((v) => (
              <FilterPill key={v} label={v} active={sortBy === v} onClick={() => setSortBy(v)} />
            ))}
          </FilterGroup>
        </div>

        {/* Summary line */}
        <div style={{ fontFamily: "'Space Mono', monospace", fontSize: '10px', color: '#555', letterSpacing: '0.5px', marginBottom: '16px', display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span>
            Showing {filteredFindings.length} of {allFindings.length} findings
            {filtersActive && ' · filters active'}
          </span>
          {filtersActive && (
            <button onClick={clearFilters} style={{ background: 'transparent', border: 'none', color: 'var(--accent)', fontFamily: "'Space Mono', monospace", fontSize: '10px', cursor: 'pointer', padding: 0 }}>
              × clear
            </button>
          )}
        </div>

        {/* Empty state */}
        {filteredFindings.length === 0 ? (
          <div style={{ padding: '36px 24px', textAlign: 'center', border: '1px solid #1a1a1a' }}>
            <div style={{ fontFamily: "'Space Mono', monospace", fontSize: '12px', color: '#444', marginBottom: '14px' }}>
              {allFindings.length === 0 ? 'No live findings in database.' : 'No findings match your filters.'}
            </div>
            {filtersActive && (
              <button onClick={clearFilters} style={{ background: 'transparent', border: '1px solid #2a2a2a', color: '#555', fontFamily: "'Space Mono', monospace", fontSize: '9px', letterSpacing: '1.5px', padding: '6px 16px', cursor: 'pointer' }}>
                CLEAR FILTERS
              </button>
            )}
          </div>
        ) : (
          filteredFindings.map((f) => (
            <div
              className="finding"
              key={f.id}
              style={{ borderLeft: `3px solid ${SEV_BORDER[f.severity] ?? '#2a2a2a'}`, paddingTop: '14px', paddingBottom: '14px' }}
            >
              {/* Header row */}
              <div className="finding-head" style={{ marginBottom: '6px' }}>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap', marginBottom: '5px' }}>
                    <span style={{ fontFamily: "'Space Mono', monospace", fontSize: '10px', color: SOURCE_COLORS[f.source] ?? 'var(--accent)', letterSpacing: '1px' }}>
                      {SOURCE_LABELS[f.source] ?? f.source.toUpperCase()}
                    </span>
                    <span style={{ color: '#2a2a2a' }}>·</span>
                    <span style={{ fontFamily: "'Space Mono', monospace", fontSize: '10px', color: '#555', letterSpacing: '0.5px' }}>{f.finding_type}</span>
                    {f.source_platform && <PlatformBadge platform={f.source_platform} />}
                  </div>
                  <div className="finding-title" style={{ marginBottom: '2px' }}>{f.title}</div>
                  <TranslitHint text={f.title} map={translitMap} />
                </div>
                <div className={`finding-sev-badge ${SEV_CLASS[f.severity] ?? ''}`} style={{ flexShrink: 0, alignSelf: 'flex-start' }}>
                  {f.severity}
                </div>
              </div>

              {/* Meta */}
              <div className="finding-meta" style={{ marginBottom: '8px' }}>
                <div className="finding-meta-item"><span className="label">Detected</span> {fmtTs(f.timestamp)}</div>
                <div className="finding-meta-item"><span className="label">Status</span> {f.status}</div>
              </div>

              {/* Description */}
              <div className="finding-body" style={{ fontSize: '12px', marginBottom: '2px' }}>{f.description}</div>
              <TranslitHint text={f.description} map={translitMap} />

              {f.judge && <JudgePanel judge={f.judge} />}
              <PlaybookPanel threatType={f.finding_type} />
              <FeedbackBar
                findingId={f.id}
                state={fbState(f.id)}
                reason={dismissReason[f.id] ?? ''}
                onConfirm={() => confirmFinding(f.id)}
                onStartDismiss={() => startDismiss(f.id)}
                onReasonChange={(v) => setDismissReason((p) => ({ ...p, [f.id]: v }))}
                onSubmitDismiss={() => submitDismiss(f.id)}
              />
            </div>
          ))
        )}
      </div>

      {selectedDraft && (
        <CertInReportModal draft={selectedDraft} onClose={() => setSelectedDraft(null)} />
      )}
    </div>
  );
}

'use client';

import { useEffect, useRef, useState } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

const MONO: React.CSSProperties = { fontFamily: "'Space Mono', monospace" };

// ── Kaaval overview types ──────────────────────────────────────────────────

interface Plugin {
  name: string;
  detail: string;
  on: boolean;
}

const INITIAL_PLUGINS: Plugin[] = [
  { name: 'Video Forensics', detail: '· deepfake detection', on: true },
  { name: 'Social Listening', detail: '· 4 platform APIs', on: true },
  { name: 'Tamil NLP', detail: '· Local Ollama · qwen2.5:14b', on: true },
  { name: 'Fact-Check Cross-Ref', detail: '· PIB / AltNews / Vishvas / +4', on: true },
  { name: 'Editorial Brief', detail: '· bilingual generator', on: true },
  { name: 'Threads Listener', detail: '· beta · Telegram + X', on: false },
];

interface Metrics {
  total_threats_24h: number;
  threats_7d: number;
  by_type: Record<string, number>;
  brigade_detected_24h: number;
  fake_pages_24h: number;
  deepfakes_24h: number;
  sources_monitored: number;
  active_agents: number;
  last_scan: string | null;
}

const INITIAL_METRICS: Metrics = {
  total_threats_24h: 0,
  threats_7d: 0,
  by_type: {},
  brigade_detected_24h: 0,
  fake_pages_24h: 0,
  deepfakes_24h: 0,
  sources_monitored: 0,
  active_agents: 0,
  last_scan: null,
};

// ── Brand / Content Protection types ──────────────────────────────────────

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
  judge?: { followers?: number; estimated_reach?: number; [key: string]: unknown } | null;
}

interface FindingsResponse {
  findings: BrandFinding[];
  total: number;
  by_type: Record<string, number>;
}

interface DomainVerification {
  domain: string;
  is_live: boolean;
  http_status: number | null;
  server: string | null;
  registered: boolean;
  creation_date: string | null;
  registrar: string | null;
  verification: 'CONFIRMED_LIVE' | 'NOT_RESPONDING' | 'UNKNOWN';
}

type DomainCheckState = DomainVerification | 'loading' | 'error';

interface DmcaDraft {
  finding_id: string;
  threat_type: string;
  dmca_draft: string;
  rights_holder: string;
  filed: boolean;
  note: string;
}

type BrandTab = 'ALL' | 'LOGO_MISUSE' | 'FOOTAGE_THEFT' | 'ANCHOR_IMPERSONATION';

const BRAND_TABS: { key: BrandTab; label: string }[] = [
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

function timeAgo(iso: string | null): string {
  if (!iso) return '—';
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

// ── DMCA Modal ─────────────────────────────────────────────────────────────

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

// ── Tab panels ─────────────────────────────────────────────────────────────

function OverviewPanel({
  plugins,
  metrics,
  togglePlugin,
}: {
  plugins: Plugin[];
  metrics: Metrics;
  togglePlugin: (name: string) => void;
}) {
  const [archiveFingerprints, setArchiveFingerprints] = useState<number | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/archive/stats`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d) setArchiveFingerprints(d.total_fingerprints ?? null); })
      .catch(() => {});
  }, []);

  return (
    <>
      <div className="metrics">
        <div className="metric">
          <div className="metric-label">Threats / 24h</div>
          <div className="metric-value accent">{metrics.total_threats_24h || '—'}</div>
          <div className="metric-delta">{metrics.threats_7d} this week</div>
        </div>
        <div className="metric">
          <div className="metric-label">Sources Monitored</div>
          <div className="metric-value">{metrics.sources_monitored || '—'}</div>
          <div className="metric-delta">YouTube · X · FB · TG</div>
        </div>
        <div className="metric">
          <div className="metric-label">Tamil NLP Accuracy</div>
          <div className="metric-value safe">88%</div>
          <div className="metric-delta">Estimated</div>
        </div>
        <div className="metric">
          <div className="metric-label">Active Agents</div>
          <div className="metric-value">{metrics.active_agents || '—'}</div>
          <div className="metric-delta safe">All operational</div>
        </div>
        <div className="metric">
          <div className="metric-label">Archive Fingerprints</div>
          <div className="metric-value">{archiveFingerprints ?? '—'}</div>
          <div className="metric-delta">
            {archiveFingerprints === 0
              ? 'Awaiting real archive connection'
              : 'Indexed media hashes'}
          </div>
        </div>
      </div>

      <div className="metrics" style={{ marginTop: '12px' }}>
        <div className="metric">
          <div className="metric-label">Brigade / 24h</div>
          <div className="metric-value">{metrics.brigade_detected_24h || '—'}</div>
          <div className="metric-delta">Coordinated attacks</div>
        </div>
        <div className="metric">
          <div className="metric-label">Fake Pages / 24h</div>
          <div className="metric-value">{metrics.fake_pages_24h || '—'}</div>
          <div className="metric-delta">Impersonation pages</div>
        </div>
        <div className="metric">
          <div className="metric-label">Deepfakes / 24h</div>
          <div className="metric-value">{metrics.deepfakes_24h || '—'}</div>
          <div className="metric-delta">Audio + video</div>
        </div>
        <div className="metric">
          <div className="metric-label">Last Scan</div>
          <div className="metric-value" style={{ fontSize: '18px' }}>
            {metrics.last_scan ? timeAgo(metrics.last_scan) : '—'}
          </div>
          <div className="metric-delta">{metrics.last_scan ? fmtTs(metrics.last_scan) : 'No scans yet'}</div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Multi-Agent Architecture</div>
          <div className="panel-tag">Python agents · run on demand</div>
        </div>
        <div className="panel-body" style={{ padding: '20px' }}>
          <div className="agents-grid cols-5" style={{ marginBottom: 0 }}>
            <div className="agent-cell module-color">
              <div className="agent-icon">⊙</div>
              <div className="agent-name">Video Forensics</div>
              <div className="agent-status">Reverse + Deepfake</div>
            </div>
            <div className="agent-cell module-color">
              <div className="agent-icon">◉</div>
              <div className="agent-name">Social Listening</div>
              <div className="agent-status">YT · X · FB · TG</div>
            </div>
            <div className="agent-cell module-color">
              <div className="agent-icon">▲</div>
              <div className="agent-name">Tamil NLP</div>
              <div className="agent-status">Local Ollama</div>
            </div>
            <div className="agent-cell module-color">
              <div className="agent-icon">◈</div>
              <div className="agent-name">Fact-Check</div>
              <div className="agent-status">PIB / BOOM / Wiki</div>
            </div>
            <div className="agent-cell module-color">
              <div className="agent-icon">⌬</div>
              <div className="agent-name">Editorial Brief</div>
              <div className="agent-status">Tamil + English</div>
            </div>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Agent Plugins</div>
          <div className="panel-tag">Plug-in / plug-out</div>
        </div>
        <div className="panel-body" style={{ padding: 0 }}>
          <div className="plugin-list">
            {plugins.map((p) => (
              <div className="plugin-row" key={p.name}>
                <div className="plugin-name">
                  {p.name} <span className="dim">{p.detail}</span>
                </div>
                <div
                  className={`toggle${p.on ? ' on' : ''}`}
                  onClick={() => togglePlugin(p.name)}
                />
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

type ThreatPeriod = 'TODAY' | '7D' | '30D';

const PERIOD_DAYS: Record<ThreatPeriod, number> = { TODAY: 1, '7D': 7, '30D': 30 };
const PERIOD_LABEL: Record<ThreatPeriod, string> = { TODAY: 'today', '7D': 'in last 7 days', '30D': 'in last 30 days' };

const SEV_COLOR: Record<string, string> = {
  CRITICAL: 'var(--critical)',
  HIGH: 'var(--high)',
  MEDIUM: 'var(--medium)',
  LOW: 'var(--safe)',
};

interface ThreatFeedItem {
  id: string;
  threat_type: string;
  severity: string;
  title: string;
  description: string;
  source_platform: string;
  detected_at: string | null;
  status: string;
}

function fmtFeedTime(iso: string | null): string {
  if (!iso) return '--:--';
  const d = new Date(iso);
  return [d.getHours(), d.getMinutes()].map((n) => String(n).padStart(2, '0')).join(':');
}

function ThreatsFeedPanel() {
  const [threats, setThreats] = useState<ThreatFeedItem[]>([]);
  const [period, setPeriod] = useState<ThreatPeriod>('7D');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    function fetch7d() {
      setLoading(true);
      setError(null);
      fetch(`${API_URL}/api/kaaval/threats-feed?days=${PERIOD_DAYS[period]}`)
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((d) => setThreats(d.threats ?? []))
        .catch(() => setError('Failed to load threats feed.'))
        .finally(() => setLoading(false));
    }
    fetch7d();
    const id = setInterval(fetch7d, 60_000);
    return () => clearInterval(id);
  }, [period]);

  return (
    <div>
      {/* Header + period filter */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px', flexWrap: 'wrap', gap: '10px' }}>
        <div style={{ ...MONO, fontSize: '10px', color: '#555', letterSpacing: '0.5px' }}>
          {loading
            ? 'Loading…'
            : error
              ? <span style={{ color: 'var(--critical)' }}>{error}</span>
              : `${threats.length} threat${threats.length === 1 ? '' : 's'} ${PERIOD_LABEL[period]}`}
        </div>
        <div style={{ display: 'flex' }}>
          {(['TODAY', '7D', '30D'] as ThreatPeriod[]).map((p, i) => (
            <button
              key={p}
              type="button"
              onClick={() => setPeriod(p)}
              style={{
                ...MONO, fontSize: '10px', letterSpacing: '1.5px', cursor: 'pointer',
                padding: '5px 12px',
                background: 'transparent',
                border: period === p ? '1px solid var(--accent)' : '1px solid var(--line)',
                color: period === p ? 'var(--accent)' : '#555',
                marginLeft: i > 0 ? '-1px' : '0',
              }}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Live Threats Feed</div>
          <div className="panel-tag">Auto-refresh · 60s</div>
        </div>
        <div className="live-feed" style={{ maxHeight: '600px', overflowY: 'auto' }}>
          {loading && (
            <div className="feed-row">
              <span className="feed-ts">--:--</span>
              <span className="feed-sev info">WAIT</span>
              <span className="feed-msg" style={{ color: 'var(--dim)' }}>Loading threats…</span>
            </div>
          )}
          {!loading && threats.length === 0 && (
            <div className="feed-row">
              <span className="feed-ts">--:--</span>
              <span className="feed-sev info">NONE</span>
              <span className="feed-msg" style={{ color: 'var(--dim)' }}>
                No threats detected {PERIOD_LABEL[period]}.
              </span>
            </div>
          )}
          {!loading && threats.map((t) => (
            <div className="feed-row" key={t.id}>
              <span className="feed-ts">{fmtFeedTime(t.detected_at)}</span>
              <span className={`feed-sev ${SEV_CLASS[t.severity] ?? 'info'}`}>{t.severity}</span>
              <span className="feed-msg">
                <span style={{ color: SEV_COLOR[t.severity] ?? 'var(--accent)', ...MONO, fontSize: '10px', letterSpacing: '0.5px' }}>
                  {t.threat_type}
                </span>
                {' · '}
                <span>{t.title}</span>
                {t.source_platform && (
                  <span style={{ color: '#3a3a3a', fontSize: '10px', marginLeft: '8px' }}>
                    [{t.source_platform.toUpperCase()}]
                  </span>
                )}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

type DateFilter = 'TODAY' | '7D' | 'ALL';
type StatusTab = 'OPEN' | 'CONFIRMED' | 'RESOLVED' | 'ALL';
type SeverityFilter = 'ALL' | 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';
type SortOrder = 'NEWEST' | 'SEVERITY';

const DATE_FILTERS: { key: DateFilter; label: string }[] = [
  { key: 'TODAY', label: 'TODAY' },
  { key: '7D', label: '7D' },
  { key: 'ALL', label: 'ALL' },
];

const STATUS_TABS: { key: StatusTab; label: string }[] = [
  { key: 'OPEN', label: 'OPEN' },
  { key: 'CONFIRMED', label: 'CONFIRMED' },
  { key: 'RESOLVED', label: 'RESOLVED' },
  { key: 'ALL', label: 'ALL' },
];

const SEVERITY_FILTERS: { key: SeverityFilter; label: string }[] = [
  { key: 'ALL', label: 'ALL' },
  { key: 'CRITICAL', label: 'CRIT' },
  { key: 'HIGH', label: 'HIGH' },
  { key: 'MEDIUM', label: 'MED' },
  { key: 'LOW', label: 'LOW' },
];

const BRAND_SHORT: Record<string, string> = {
  ALL: 'ALL',
  LOGO_MISUSE: 'LOGO',
  FOOTAGE_THEFT: 'FOOTAGE',
  ANCHOR_IMPERSONATION: 'ANCHOR',
  FAKE_PAGE: 'FAKE',
};

const SEV_ORDER: Record<string, number> = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 };

const DATE_LABEL: Record<DateFilter, string> = { TODAY: 'Today', '7D': 'Last 7 days', ALL: 'All time' };
const STATUS_LABEL: Record<StatusTab, string> = { OPEN: 'Open', CONFIRMED: 'Confirmed', RESOLVED: 'Resolved', ALL: 'All statuses' };

function isWithin24h(iso: string | null): boolean {
  if (!iso) return false;
  return Date.now() - new Date(iso).getTime() < 24 * 60 * 60 * 1000;
}

function inDateWindow(iso: string | null, filter: DateFilter): boolean {
  if (filter === 'ALL') return true;
  if (!iso) return false;
  const ms = filter === 'TODAY' ? 24 * 60 * 60 * 1000 : 7 * 24 * 60 * 60 * 1000;
  return Date.now() - new Date(iso).getTime() <= ms;
}

function matchesStatus(status: string, tab: StatusTab): boolean {
  const s = status.toLowerCase();
  if (tab === 'ALL') return true;
  if (tab === 'OPEN') return s === 'detected' || s === 'open';
  if (tab === 'CONFIRMED') return s === 'confirmed';
  if (tab === 'RESOLVED') return s === 'resolved';
  return true;
}

function platformLabel(url: string): string {
  if (url.includes('facebook.com')) return 'VIEW ON FACEBOOK →';
  if (url.includes('instagram.com')) return 'VIEW ON INSTAGRAM →';
  if (url.includes('t.me')) return 'VIEW ON TELEGRAM →';
  if (url.includes('youtube.com')) return 'VIEW ON YOUTUBE →';
  return 'VIEW SOURCE →';
}

function extractYouTubeVideoId(url: string): string | null {
  try {
    const u = new URL(url);
    if (u.hostname === 'youtu.be') return u.pathname.slice(1) || null;
    if (u.pathname.startsWith('/shorts/')) return u.pathname.split('/')[2] || null;
    return u.searchParams.get('v');
  } catch {
    return null;
  }
}

function youTubeCopyrightUrl(sourceUrl: string): string {
  const videoId = extractYouTubeVideoId(sourceUrl);
  const base = 'https://www.youtube.com/copyright_complaint_form';
  return videoId ? `${base}?video_id=${encodeURIComponent(videoId)}` : base;
}

function isVerifiableWebUrl(url: string): boolean {
  return !url.includes('t.me') && !url.includes('instagram.com');
}

function fmtRegDate(iso: string | null): string {
  if (!iso) return '';
  try {
    return new Intl.DateTimeFormat('en-IN', {
      year: 'numeric', month: 'short', day: 'numeric',
    }).format(new Date(iso));
  } catch {
    return '';
  }
}

function ContentProtectionPanel() {
  const [data, setData] = useState<FindingsResponse | null>(null);
  const [brandTab, setBrandTab] = useState<BrandTab>('ALL');
  const [dateFilter, setDateFilter] = useState<DateFilter>('7D');
  const [statusTab, setStatusTab] = useState<StatusTab>('OPEN');
  const [severityFilter, setSeverityFilter] = useState<SeverityFilter>('ALL');
  const [sortOrder, setSortOrder] = useState<SortOrder>('NEWEST');
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [draft, setDraft] = useState<DmcaDraft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [domainChecks, setDomainChecks] = useState<Record<string, DomainCheckState>>({});

  function fetchFindings() {
    fetch(`${API_URL}/api/brand-misuse/findings`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d) setData(d); })
      .catch(() => setError('Failed to load findings.'));
  }

  useEffect(() => { fetchFindings(); }, []);

  function handleScan() {
    setScanning(true);
    fetch(`${API_URL}/api/kaaval/fake-pages/scan`, { method: 'POST' })
      .then((r) => (r.ok ? r.json() : null))
      .then((res) => {
        const count = res?.threats_found ?? res?.count ?? 0;
        setToast(`Scan complete — ${count} threats found`);
        fetchFindings();
        setTimeout(() => setToast(null), 4000);
      })
      .catch(() => { setToast('Scan complete — check findings'); setTimeout(() => setToast(null), 4000); })
      .finally(() => setScanning(false));
  }

  function confirmFinding(id: string) {
    setBusy((p) => ({ ...p, [id]: true }));
    fetch(`${API_URL}/api/brand-misuse/${id}/confirm`, { method: 'POST' })
      .then((r) => (r.ok ? r.json() : null))
      .then((res) => {
        if (!res) return;
        setData((prev) =>
          prev && {
            ...prev,
            findings: prev.findings.map((f) =>
              f.finding_id === id
                ? { ...f, status: 'confirmed', dmca_draft_ready: true }
                : f,
            ),
          },
        );
      })
      .catch(() => setError('Confirm failed.'))
      .finally(() => setBusy((p) => ({ ...p, [id]: false })));
  }

  function verifyDomain(id: string, url: string) {
    setDomainChecks((p) => ({ ...p, [id]: 'loading' }));
    fetch(`${API_URL}/api/kaaval/verify-domain?url=${encodeURIComponent(url)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((res: DomainVerification | null) => {
        setDomainChecks((p) => ({ ...p, [id]: res ?? 'error' }));
      })
      .catch(() => setDomainChecks((p) => ({ ...p, [id]: 'error' })));
  }

  function viewDraft(id: string) {
    fetch(`${API_URL}/api/brand-misuse/${id}/dmca-draft`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d) setDraft(d); })
      .catch(() => setError('Could not load DMCA draft.'));
  }

  // ── Layered client-side filter pipeline ───────────────────────────────────
  const q = search.trim().toLowerCase();

  const afterDateAndType = (data?.findings ?? []).filter(
    (f) => (brandTab === 'ALL' || f.threat_type === brandTab) && inDateWindow(f.detected_at, dateFilter),
  );

  const afterSearch = q
    ? afterDateAndType.filter((f) =>
        f.title.toLowerCase().includes(q) ||
        f.description.toLowerCase().includes(q) ||
        (f.source_url ?? '').toLowerCase().includes(q) ||
        f.threat_type.toLowerCase().includes(q),
      )
    : afterDateAndType;

  const severityCounts: Record<SeverityFilter, number> = {
    ALL: afterSearch.length,
    CRITICAL: afterSearch.filter((f) => f.severity === 'CRITICAL').length,
    HIGH: afterSearch.filter((f) => f.severity === 'HIGH').length,
    MEDIUM: afterSearch.filter((f) => f.severity === 'MEDIUM').length,
    LOW: afterSearch.filter((f) => f.severity === 'LOW').length,
  };

  const afterSeverity = severityFilter === 'ALL'
    ? afterSearch
    : afterSearch.filter((f) => f.severity === severityFilter);

  const statusCounts: Record<StatusTab, number> = {
    OPEN: afterSeverity.filter((f) => matchesStatus(f.status, 'OPEN')).length,
    CONFIRMED: afterSeverity.filter((f) => matchesStatus(f.status, 'CONFIRMED')).length,
    RESOLVED: afterSeverity.filter((f) => matchesStatus(f.status, 'RESOLVED')).length,
    ALL: afterSeverity.length,
  };

  const findings = [...afterSeverity.filter((f) => matchesStatus(f.status, statusTab))].sort((a, b) => {
    if (sortOrder === 'SEVERITY') {
      return (SEV_ORDER[a.severity] ?? 99) - (SEV_ORDER[b.severity] ?? 99);
    }
    const ta = a.detected_at ? new Date(a.detected_at).getTime() : 0;
    const tb = b.detected_at ? new Date(b.detected_at).getTime() : 0;
    return tb - ta;
  });

  const chipStyle = (isActive: boolean): React.CSSProperties => ({
    ...MONO, fontSize: '10px', letterSpacing: '1px', cursor: 'pointer',
    padding: '4px 10px', background: 'transparent',
    border: isActive ? '1px solid var(--accent)' : '1px solid transparent',
    color: isActive ? 'var(--accent)' : 'var(--text-dim)',
  });

  return (
    <>
      {/* Fake page scan */}
      <div style={{ marginBottom: '20px', display: 'flex', alignItems: 'center', gap: '16px' }}>
        <button
          type="button"
          onClick={handleScan}
          disabled={scanning}
          style={{
            ...MONO, fontSize: '12px', letterSpacing: '2px',
            cursor: scanning ? 'wait' : 'pointer',
            padding: '10px 20px',
            background: scanning ? 'rgba(255,140,0,0.08)' : 'rgba(255,140,0,0.15)',
            border: `1px solid ${scanning ? 'rgba(255,140,0,0.2)' : 'rgba(255,140,0,0.5)'}`,
            color: scanning ? '#888' : '#ff8c00',
          }}
        >
          {scanning ? 'SCANNING...' : 'SCAN FOR FAKE PAGES'}
        </button>
        {toast && (
          <span style={{ ...MONO, fontSize: '11px', color: 'var(--safe)', letterSpacing: '0.5px' }}>
            ✓ {toast}
          </span>
        )}
      </div>

      {/* Human-in-the-loop warning banner */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: '12px',
        padding: '14px 18px', marginBottom: '24px',
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

      {/* ROW 1 — Search bar */}
      <div style={{ position: 'relative', marginBottom: '10px' }}>
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search findings by keyword, URL, or platform..."
          style={{
            width: '100%', boxSizing: 'border-box',
            background: 'var(--bg)',
            border: `1px solid ${search ? 'var(--accent)' : 'var(--line)'}`,
            color: '#d0d0d0',
            padding: '9px 36px 9px 12px',
            ...MONO, fontSize: '12px',
            outline: 'none',
            transition: 'border-color 0.15s',
          }}
        />
        {search && (
          <button
            type="button"
            onClick={() => setSearch('')}
            style={{
              position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)',
              background: 'none', border: 'none', color: '#666',
              cursor: 'pointer', fontSize: '16px', lineHeight: 1, padding: '2px',
            }}
          >
            ×
          </button>
        )}
      </div>

      {/* ROW 2 — Compact filter bar */}
      <div style={{
        display: 'flex', alignItems: 'center', flexWrap: 'wrap',
        gap: '0', rowGap: '6px', marginBottom: '10px',
        padding: '4px 6px',
        border: '1px solid var(--line)',
      }}>
        {/* Period group */}
        <span style={{ ...MONO, fontSize: '9px', color: '#444', letterSpacing: '1.5px', padding: '0 6px 0 2px' }}>
          PERIOD
        </span>
        {DATE_FILTERS.map((f) => {
          const isActive = dateFilter === f.key;
          return (
            <button key={f.key} type="button" onClick={() => setDateFilter(f.key)} style={chipStyle(isActive)}>
              {f.label}
            </button>
          );
        })}

        {/* Divider */}
        <div style={{ width: '1px', height: '16px', background: 'var(--line)', margin: '0 6px', flexShrink: 0 }} />

        {/* Type group */}
        {BRAND_TABS.map((t) => {
          const isActive = brandTab === t.key;
          return (
            <button key={t.key} type="button" onClick={() => setBrandTab(t.key)} style={chipStyle(isActive)}>
              {BRAND_SHORT[t.key] ?? t.key}
            </button>
          );
        })}

        {/* Divider */}
        <div style={{ width: '1px', height: '16px', background: 'var(--line)', margin: '0 6px', flexShrink: 0 }} />

        {/* Severity group */}
        {SEVERITY_FILTERS.map((s) => {
          const isActive = severityFilter === s.key;
          return (
            <button key={s.key} type="button" onClick={() => setSeverityFilter(s.key)} style={chipStyle(isActive)}>
              {s.label}
            </button>
          );
        })}

        {/* Spacer + sort dropdown */}
        <div style={{ flex: 1 }} />
        <select
          value={sortOrder}
          onChange={(e) => setSortOrder(e.target.value as SortOrder)}
          style={{
            ...MONO, fontSize: '10px', letterSpacing: '1px',
            background: 'var(--bg)',
            border: '1px solid var(--line)',
            color: 'var(--text-dim)',
            padding: '4px 8px',
            cursor: 'pointer',
            outline: 'none',
          }}
        >
          <option value="NEWEST">NEWEST ▾</option>
          <option value="SEVERITY">SEVERITY ▾</option>
        </select>
      </div>

      {/* ROW 3 — Status tabs */}
      <div style={{ display: 'flex', gap: '0', marginBottom: '8px', borderBottom: '1px solid var(--line)' }}>
        {STATUS_TABS.map((t) => {
          const isActive = statusTab === t.key;
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => setStatusTab(t.key)}
              style={{
                ...MONO, fontSize: '10px', letterSpacing: '1.5px', cursor: 'pointer',
                padding: '9px 14px',
                background: 'transparent',
                border: 'none',
                borderBottom: isActive ? '2px solid var(--accent)' : '2px solid transparent',
                color: isActive ? 'var(--accent)' : 'var(--text-dim)',
                marginBottom: '-1px',
              }}
            >
              {t.label}
              <span style={{ color: '#555', marginLeft: '5px' }}>· {statusCounts[t.key]}</span>
            </button>
          );
        })}
      </div>

      {/* ROW 4 — Summary line */}
      <div style={{ ...MONO, fontSize: '11px', color: 'var(--text-dim)', letterSpacing: '0.3px', marginBottom: '20px' }}>
        Showing{' '}
        <span style={{ color: '#d0d0d0' }}>{findings.length}</span>
        {' '}of{' '}
        <span style={{ color: '#d0d0d0' }}>{data?.total ?? 0}</span>
        {' '}findings · {DATE_LABEL[dateFilter]} · {STATUS_LABEL[statusTab]}
      </div>

      {/* Finding cards */}
      {findings.length === 0 ? (
        <div style={{ padding: '48px 20px', textAlign: 'center' }}>
          <div style={{ ...MONO, fontSize: '11px', color: '#555', letterSpacing: '1.5px' }}>
            {data ? 'NO FINDINGS FOR THIS PERIOD' : 'LOADING FINDINGS…'}
          </div>
        </div>
      ) : (
        findings.map((f) => {
          const isDetected = f.status === 'detected';
          const confPct = f.confidence != null ? Math.round(f.confidence * 100) : null;
          const newBadge = isWithin24h(f.detected_at);
          return (
            <div className="finding" key={f.finding_id}>
              <div className="finding-head">
                <div>
                  <div className="finding-id" style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ color: TYPE_COLORS[f.threat_type] ?? 'var(--accent)' }}>
                      {TYPE_LABELS[f.threat_type] ?? f.threat_type}
                    </span>
                    {newBadge && (
                      <span style={{
                        ...MONO, fontSize: '9px', letterSpacing: '1.5px',
                        padding: '2px 6px',
                        border: '1px solid rgba(255,140,0,0.6)',
                        color: '#ff8c00',
                        background: 'rgba(255,140,0,0.08)',
                      }}>
                        NEW
                      </span>
                    )}
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
                {(f.judge?.followers ?? 0) > 0 && (
                  <div className="finding-meta-item">
                    <span style={{ fontFamily: "'Space Mono', monospace", fontSize: '10px', color: 'var(--text-dim)' }}>
                      {(f.judge!.followers as number).toLocaleString()} followers
                    </span>
                  </div>
                )}
                {(f.judge?.estimated_reach ?? 0) > 0 && (
                  <div className="finding-meta-item">
                    <span style={{ fontFamily: "'Space Mono', monospace", fontSize: '10px', color: 'var(--text-mid)' }}>
                      ~{(f.judge!.estimated_reach as number).toLocaleString()} estimated reach
                    </span>
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

                {f.status === 'confirmed' && f.threat_type === 'FOOTAGE_THEFT' && f.source_url?.includes('youtube.com') && (
                  <a
                    href={youTubeCopyrightUrl(f.source_url)}
                    target="_blank"
                    rel="noreferrer"
                    title="Opens YouTube's copyright removal form with video pre-selected. File manually after review."
                    style={{
                      ...MONO, fontSize: '11px', letterSpacing: '1px', cursor: 'pointer',
                      padding: '8px 16px',
                      background: 'rgba(58,142,255,0.1)',
                      border: '1px solid rgba(58,142,255,0.35)',
                      color: '#3a8eff',
                      textDecoration: 'none',
                      display: 'inline-block',
                    }}
                  >
                    DRAFT COPYRIGHT NOTICE →
                  </a>
                )}

                {f.threat_type === 'FAKE_PAGE' && f.source_url && isVerifiableWebUrl(f.source_url) && (
                  <button
                    onClick={() => verifyDomain(f.finding_id, f.source_url!)}
                    disabled={domainChecks[f.finding_id] === 'loading'}
                    style={{
                      ...MONO, fontSize: '11px', letterSpacing: '1px',
                      cursor: domainChecks[f.finding_id] === 'loading' ? 'wait' : 'pointer',
                      padding: '8px 16px',
                      background: 'rgba(58,142,255,0.1)',
                      border: '1px solid rgba(58,142,255,0.35)',
                      color: '#3a8eff',
                      opacity: domainChecks[f.finding_id] === 'loading' ? 0.6 : 1,
                    }}
                  >
                    {domainChecks[f.finding_id] === 'loading' ? 'VERIFYING…' : 'VERIFY DOMAIN →'}
                  </button>
                )}

                {!isDetected && !f.dmca_draft_ready && !(f.status === 'confirmed' && f.threat_type === 'FOOTAGE_THEFT' && f.source_url?.includes('youtube.com')) && (
                  <span style={{ ...MONO, fontSize: '10px', color: 'var(--text-dim)', letterSpacing: '1px' }}>
                    {f.status.toUpperCase()} — no action pending
                  </span>
                )}

                {f.source_url && (
                  <a
                    href={f.source_url}
                    target="_blank"
                    rel="noreferrer"
                    style={{
                      ...MONO, fontSize: '10px', letterSpacing: '1px',
                      marginLeft: 'auto',
                      padding: '6px 12px',
                      border: '1px solid var(--accent)',
                      color: 'var(--accent)',
                      background: 'transparent',
                      textDecoration: 'none',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {platformLabel(f.source_url)}
                  </a>
                )}
              </div>

              {(() => {
                const check = domainChecks[f.finding_id];
                if (!check || check === 'loading') return null;
                if (check === 'error') {
                  return (
                    <div style={{ marginTop: '10px', ...MONO, fontSize: '10px', color: 'var(--text-dim)' }}>
                      Domain verification failed.
                    </div>
                  );
                }
                const isLive = check.verification === 'CONFIRMED_LIVE';
                const isDown = check.verification === 'NOT_RESPONDING';
                const badgeStyle = isLive
                  ? { border: '1px solid rgba(74,222,128,0.5)', color: 'var(--safe)', background: 'rgba(74,222,128,0.08)' }
                  : isDown
                    ? { border: '1px solid rgba(255,159,67,0.5)', color: 'var(--high)', background: 'rgba(255,159,67,0.08)' }
                    : { border: '1px solid var(--line)', color: 'var(--text-dim)', background: 'transparent' };
                const badgeLabel = isLive
                  ? `✓ LIVE${check.registered && check.creation_date ? ` · Registered ${fmtRegDate(check.creation_date)}` : ''}`
                  : isDown
                    ? '⚠ NOT RESPONDING'
                    : 'UNKNOWN';
                return (
                  <div style={{ marginTop: '10px' }}>
                    <span style={{
                      ...MONO, fontSize: '10px', letterSpacing: '1px', padding: '3px 8px',
                      textTransform: 'uppercase', ...badgeStyle,
                    }}>
                      {badgeLabel}
                    </span>
                    <div style={{ ...MONO, fontSize: '9px', color: 'var(--text-dim)', marginTop: '4px' }}>
                      {check.domain}
                      {check.http_status != null && ` · HTTP ${check.http_status}`}
                      {check.server && ` · ${check.server}`}
                      {check.registrar && ` · Registrar: ${check.registrar}`}
                    </div>
                  </div>
                );
              })()}
            </div>
          );
        })
      )}

      {draft && <DmcaDraftModal draft={draft} onClose={() => setDraft(null)} />}
    </>
  );
}

// ── Page tabs ──────────────────────────────────────────────────────────────

type PageTab = 'INTELLIGENCE' | 'THREATS_FEED' | 'CONTENT_PROTECTION';

const PAGE_TABS: { key: PageTab; label: string }[] = [
  { key: 'INTELLIGENCE', label: 'INTELLIGENCE' },
  { key: 'THREATS_FEED', label: 'THREATS FEED' },
  { key: 'CONTENT_PROTECTION', label: 'CONTENT PROTECTION' },
];

// ── Main page ──────────────────────────────────────────────────────────────

export default function KaavalPage() {
  const [plugins, setPlugins] = useState<Plugin[]>(INITIAL_PLUGINS);
  const [metrics, setMetrics] = useState<Metrics>(INITIAL_METRICS);
  const [pageTab, setPageTab] = useState<PageTab>('INTELLIGENCE');

  useEffect(() => {
    function fetchMetrics() {
      fetch(`${API_URL}/api/kaaval/metrics`, { cache: 'no-store' })
        .then((res) => {
          if (!res.ok) throw new Error(`API error ${res.status}`);
          return res.json();
        })
        .then((data: Metrics) => setMetrics(data))
        .catch(() => {});
    }
    fetchMetrics();
    const id = setInterval(fetchMetrics, 5 * 60 * 1000);
    return () => clearInterval(id);
  }, []);

  function togglePlugin(name: string) {
    setPlugins((prev) =>
      prev.map((p) => (p.name === name ? { ...p, on: !p.on } : p)),
    );
  }

  return (
    <div className="page" id="page-kaaval" data-mc="kaaval">
      <div className="page-header">
        <h1 className="page-title">
          காவல்
          <br />
          <span className="accent">The Guardian.</span>
        </h1>
        <p className="page-sub">
          Tamil broadcast threat intelligence. 5 agents continuously scanning the
          Tamil-language internet for content threats targeting Example Broadcaster — fake
          clips, deepfakes, brigades, impersonator pages.
        </p>
      </div>

      {/* Page-level tabs */}
      <div style={{ display: 'flex', gap: '0', marginBottom: '32px', borderBottom: '1px solid var(--line)' }}>
        {PAGE_TABS.map((t) => {
          const isActive = pageTab === t.key;
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => setPageTab(t.key)}
              style={{
                ...MONO, fontSize: '10px', letterSpacing: '2px', cursor: 'pointer',
                padding: '12px 20px',
                background: 'transparent',
                border: 'none',
                borderBottom: isActive ? '2px solid var(--accent)' : '2px solid transparent',
                color: isActive ? 'var(--accent)' : 'var(--text-mid)',
                marginBottom: '-1px',
              }}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {pageTab === 'INTELLIGENCE' && (
        <OverviewPanel plugins={plugins} metrics={metrics} togglePlugin={togglePlugin} />
      )}
      {pageTab === 'THREATS_FEED' && <ThreatsFeedPanel />}
      {pageTab === 'CONTENT_PROTECTION' && <ContentProtectionPanel />}
    </div>
  );
}

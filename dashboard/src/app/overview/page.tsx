'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

interface OverviewData {
  scans_24h: number;
  critical_findings: number;
  judge_validations_24h?: number;
  endpoints_watched: number | null;
  severity_breakdown: { critical: number; high: number; medium: number; low: number };
  module_stats: {
    kaaval: { threats_24h: number; sources_monitored: number | null };
    kavasam: { channels_watched: number | null; anomalies_24h: number; cert_in_drafts: number };
    veriff: { checks_today: number; avg_verdict_time_sec: number | null };
  };
  generated_at: string;
}

interface KavasamMetrics {
  channels_watched: number;
  anomalies_24h: number;
  cert_in_drafts: number;
  dpdp_audit_days: number;
  anomalies_by_severity: Record<string, number>;
}

interface KaavalMetrics {
  total_threats_24h: number;
  by_severity: Record<string, number>;
  by_type: Record<string, number>;
  sources_monitored: number;
  active_agents: number;
}

interface VeriffMetrics {
  checks_today: number;
  avg_verdict_time: number;
  stories_held: number;
  audit_trail: string;
}

interface Anomaly {
  snapshot_id: string;
  channel_id: string;
  channel_name: string;
  platform: string;
  anomaly_type: string | null;
  anomaly_detail: string | null;
  subscriber_count: number | null;
  video_count: number | null;
  scanned_at: string | null;
}

interface Threat {
  id: string;
  channel_id: string | null;
  channel_name: string | null;
  threat_type: string;
  severity: string;
  title: string;
  description: string;
  source_platform: string;
  source_url: string | null;
  detected_at: string | null;
  status: string;
}

interface FactCheck {
  id: string;
  claim_text: string;
  media_type: string;
  language: string;
  verdict: string;
  confidence: number;
  verdict_time_seconds: number;
  database_results: unknown;
  checked_at: string | null;
  checked_by: string | null;
}

interface FeedItem {
  key: string;
  time: string | null;
  tagClass: string;
  tagLabel: string;
  text: string;
}

function threatTagClassFor(severity: string): string {
  switch (severity) {
    case 'CRITICAL': return 'critical';
    case 'HIGH':     return 'warn';
    case 'MEDIUM':   return 'info';
    default:         return 'info';
  }
}

function checkTagFor(verdict: string): { tagClass: string; tagLabel: string } {
  switch (verdict) {
    case 'REAL':        return { tagClass: 'safe', tagLabel: 'Safe' };
    case 'RECYCLED':
    case 'MANIPULATED': return { tagClass: 'warn', tagLabel: 'Warn' };
    case 'FAKE':        return { tagClass: 'critical', tagLabel: 'Critical' };
    default:            return { tagClass: 'info', tagLabel: 'Info' };
  }
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—';
  return new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit' }).format(
    new Date(iso),
  );
}

function tagClassFor(anomalyType: string | null): string {
  switch (anomalyType) {
    case 'CRITICAL': return 'critical';
    case 'HIGH':     return 'warn';
    default:         return 'info';
  }
}

function tagLabelFor(anomalyType: string | null): string {
  switch (anomalyType) {
    case 'CRITICAL': return 'Critical';
    case 'HIGH':     return 'High';
    default:         return 'Info';
  }
}

interface MetaScore {
  score: number;
  level: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  configured: boolean;
}

const META_LEVEL_COLOR: Record<string, string> = {
  LOW: 'var(--safe)',
  MEDIUM: 'var(--medium)',
  HIGH: 'var(--high)',
  CRITICAL: 'var(--critical)',
};

export default function OverviewPage() {
  const [overview, setOverview]             = useState<OverviewData | null>(null);
  const [kaavalMetrics, setKaavalMetrics]   = useState<KaavalMetrics | null>(null);
  const [kavasamMetrics, setKavasamMetrics] = useState<KavasamMetrics | null>(null);
  const [veriffMetrics, setVeriffMetrics]   = useState<VeriffMetrics | null>(null);
  const [anomalies, setAnomalies]           = useState<Anomaly[]>([]);
  const [threats, setThreats]               = useState<Threat[]>([]);
  const [checks, setChecks]                 = useState<FactCheck[]>([]);
  const [metaScore, setMetaScore]           = useState<MetaScore | null>(null);

  useEffect(() => {
    async function fetchAll() {
      const results = await Promise.allSettled([
        fetch(`${API_URL}/api/overview`).then((r) => (r.ok ? r.json() : null)),
        fetch(`${API_URL}/api/kavasam/metrics`).then((r) => (r.ok ? r.json() : null)),
        fetch(`${API_URL}/api/kaaval/metrics`).then((r) => (r.ok ? r.json() : null)),
        fetch(`${API_URL}/api/veriff/metrics`).then((r) => (r.ok ? r.json() : null)),
        fetch(`${API_URL}/api/kavasam/anomalies`).then((r) => (r.ok ? r.json() : null)),
        fetch(`${API_URL}/api/kaaval/threats`).then((r) => (r.ok ? r.json() : null)),
        fetch(`${API_URL}/api/veriff/checks`).then((r) => (r.ok ? r.json() : null)),
        fetch(`${API_URL}/api/meta/threat-score`).then((r) => (r.ok ? r.json() : null)),
      ]);
      const [ovR, kmR, kaaR, vR, aR, tR, cR, mR] = results;
      if (ovR.status  === 'fulfilled' && ovR.value)            setOverview(ovR.value);
      if (kmR.status  === 'fulfilled' && kmR.value)            setKavasamMetrics(kmR.value);
      if (kaaR.status === 'fulfilled' && kaaR.value)           setKaavalMetrics(kaaR.value);
      if (vR.status   === 'fulfilled' && vR.value)             setVeriffMetrics(vR.value);
      if (aR.status   === 'fulfilled' && aR.value?.anomalies)  setAnomalies(aR.value.anomalies);
      if (tR.status   === 'fulfilled' && tR.value?.threats)    setThreats(tR.value.threats);
      if (cR.status   === 'fulfilled' && cR.value?.checks)     setChecks(cR.value.checks);
      if (mR.status   === 'fulfilled' && mR.value)             setMetaScore(mR.value);
    }
    fetchAll().catch(() => {});
  }, []);

  const anomalyFeed: FeedItem[] = anomalies.map((a) => ({
    key: a.snapshot_id,
    time: a.scanned_at,
    tagClass: tagClassFor(a.anomaly_type),
    tagLabel: tagLabelFor(a.anomaly_type),
    text: a.anomaly_detail ?? 'No details available',
  }));

  const threatFeed: FeedItem[] = threats.slice(0, 3).map((t) => ({
    key: t.id,
    time: t.detected_at,
    tagClass: threatTagClassFor(t.severity),
    tagLabel: t.severity,
    text: `${t.title} · ${t.source_platform}`,
  }));

  const checkFeed: FeedItem[] = checks.slice(0, 3).map((c) => {
    const { tagClass, tagLabel } = checkTagFor(c.verdict);
    return {
      key: c.id,
      time: c.checked_at,
      tagClass,
      tagLabel,
      text: `${c.verdict} · ${truncate(c.claim_text, 60)}`,
    };
  });

  const feedItems = [...anomalyFeed, ...threatFeed, ...checkFeed]
    .sort((a, b) => {
      const aTime = a.time ? new Date(a.time).getTime() : 0;
      const bTime = b.time ? new Date(b.time).getTime() : 0;
      return bTime - aTime;
    })
    .slice(0, 8);

  return (
    <div className="page active" id="page-overview">
      <div className="page-header">
        <div className="page-label">PLATFORM CONSOLE</div>
        <h1 className="page-title">
          Good morning.
          <br />
          <span className="accent">
            {overview?.endpoints_watched ?? '—'} channels under watch.
          </span>
        </h1>
        <p className="page-sub">
          {overview == null
            ? 'Loading…'
            : overview.scans_24h > 0
              ? `${overview.scans_24h} scan record${overview.scans_24h === 1 ? '' : 's'} written in the last 24 hours. `
              : 'No scan activity in the last 24 hours — scans are triggered manually, not on a schedule. '}
          {overview == null
            ? ''
            : overview.critical_findings > 0
              ? `${overview.critical_findings} critical signal${overview.critical_findings === 1 ? '' : 's'} in that window — full breakdown below.`
              : 'No critical signals recorded in that window.'}
        </p>
      </div>

      <div className="metrics">
        <div className="metric">
          <div className="metric-label">Scans · 24h</div>
          <div className="metric-value accent">{overview?.scans_24h ?? '—'}</div>
          <div className="metric-delta">Threats + checks + snapshots</div>
        </div>
        <div className="metric">
          <div className="metric-label">Critical Findings</div>
          <div className="metric-value critical">{overview?.critical_findings ?? '—'}</div>
          <div className={`metric-delta${(overview?.critical_findings ?? 0) > 0 ? ' down' : ''}`}>
            {(overview?.critical_findings ?? 0) > 0 ? 'Require review' : 'None in last 24h'}
          </div>
        </div>
        <div className="metric">
          <div className="metric-label">Judge Validations · 24h</div>
          <div className="metric-value">{overview?.judge_validations_24h ?? '—'}</div>
          <div className="metric-delta">All validated by Qwen 2.5 14B</div>
        </div>
        <div className="metric">
          <div className="metric-label">Endpoints Watched</div>
          <div className="metric-value safe">{overview?.endpoints_watched ?? '—'}</div>
          <div className="metric-delta">Channels with snapshot history</div>
        </div>
        <div className="metric">
          <div className="metric-label">Meta Threat Score</div>
          {metaScore && metaScore.configured ? (
            <>
              <div
                className="metric-value"
                style={{ color: META_LEVEL_COLOR[metaScore.level] ?? 'var(--text)' }}
              >
                {metaScore.score}
              </div>
              <div
                className="metric-delta"
                style={{ color: META_LEVEL_COLOR[metaScore.level] ?? 'var(--text)' }}
              >
                {metaScore.level} · Facebook + Instagram
              </div>
            </>
          ) : (
            <>
              <div className="metric-value" style={{ color: 'var(--dim)' }}>—</div>
              <div className="metric-delta" style={{ color: 'var(--dim)' }}>NOT CONFIGURED</div>
            </>
          )}
        </div>
      </div>

      <div className="modules">
        <Link href="/kaaval" className="module-card" data-color="kaaval">
          <div className="module-status">
            <span className="module-status-dot" />
            <span className="module-status-text">Active · Monitoring</span>
          </div>
          <div className="module-tamil">காவல்  ·  The Guardian</div>
          <div className="module-name">KAAVAL AI</div>
          <div className="module-desc">
            Tamil-language threat intelligence. Tracking deepfakes, archival
            weaponisation, and coordinated brigades targeting Example Broadcaster brand.
          </div>
          <div className="module-stats">
            <div>
              <div className="module-stat-label">Threats / 24h</div>
              <div className="module-stat-value live">
                {overview?.module_stats.kaaval.threats_24h ?? '—'}
              </div>
            </div>
            <div>
              <div className="module-stat-label">Agents</div>
              <div className="module-stat-value">{kaavalMetrics?.active_agents ?? '—'}</div>
            </div>
          </div>
        </Link>

        <Link href="/kavasam" className="module-card" data-color="kavasam">
          <div className="module-status">
            <span className="module-status-dot" />
            <span className="module-status-text">Active · 24/7</span>
          </div>
          <div className="module-tamil">கவசம்  ·  The Shield</div>
          <div className="module-name">DIGITAL KAVASAM</div>
          <div className="module-desc">
            YouTube + social account protection across all four Broadcaster Network channels.
            CERT-In auto-drafter armed and ready.
          </div>
          <div className="module-stats">
            <div>
              <div className="module-stat-label">Channels</div>
              <div className="module-stat-value live">
                {overview?.module_stats.kavasam.channels_watched ?? '—'}
              </div>
            </div>
            <div>
              <div className="module-stat-label">Anomalies</div>
              <div className="module-stat-value">{kavasamMetrics?.anomalies_24h ?? '—'}</div>
            </div>
          </div>
        </Link>

        <Link href="/veriff" className="module-card" data-color="veriff">
          <div className="module-status">
            <span className="module-status-dot" />
            <span className="module-status-text">Active · Newsroom</span>
          </div>
          <div className="module-tamil">நேர்மை  ·  Truth</div>
          <div className="module-name">VERIFF TAMIL</div>
          <div className="module-desc">
            Real-time fact verification for Example News journalists. Tamil claim
            extraction across 7 fact-check databases.
          </div>
          <div className="module-stats">
            <div>
              <div className="module-stat-label">Checks Today</div>
              <div className="module-stat-value live">
                {overview?.module_stats.veriff.checks_today ?? '—'}
              </div>
            </div>
            <div>
              <div className="module-stat-label">Stories Held</div>
              <div className="module-stat-value">{veriffMetrics?.stories_held ?? '—'}</div>
            </div>
          </div>
        </Link>
      </div>

      <div className="grid-2">
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">Live Activity Feed</div>
            <div className="panel-tag">Last 4 hours · Auto-refresh</div>
          </div>
          <div className="panel-body" style={{ padding: '0 24px' }}>
            {feedItems.length === 0 ? (
              <div className="activity-item">
                <div className="activity-time">—</div>
                <div className="activity-tag info">Info</div>
                <div className="activity-text">No activity in the last 24 hours</div>
              </div>
            ) : (
              feedItems.map((item) => (
                <div className="activity-item" key={item.key}>
                  <div className="activity-time">{fmtTime(item.time)}</div>
                  <div className={`activity-tag ${item.tagClass}`}>{item.tagLabel}</div>
                  <div className="activity-text">{item.text}</div>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">Severity Matrix · 24h</div>
            <div className="panel-tag">By Module</div>
          </div>
          <div className="panel-body" style={{ padding: 0 }}>
            <div className="severity-grid">
              <div className="severity-cell">
                <div className="severity-label critical">Critical</div>
                <div className="severity-value">
                  {overview?.severity_breakdown.critical ?? '—'}
                </div>
              </div>
              <div className="severity-cell">
                <div className="severity-label high">High</div>
                <div className="severity-value">
                  {overview?.severity_breakdown.high ?? '—'}
                </div>
              </div>
              <div className="severity-cell">
                <div className="severity-label medium">Medium</div>
                <div className="severity-value">
                  {overview?.severity_breakdown.medium ?? '—'}
                </div>
              </div>
              <div className="severity-cell">
                <div className="severity-label low">Low</div>
                <div className="severity-value">
                  {overview?.severity_breakdown.low ?? '—'}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

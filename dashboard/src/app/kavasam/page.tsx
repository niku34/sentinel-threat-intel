'use client';

import { useEffect, useState } from 'react';
import {
  CertInReportModal,
  Field,
  fmtDetected,
  MONO,
  LABEL_STYLE,
  VALUE_STYLE,
  type CertInDraft,
} from '@/components/cert-in-report-modal';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
const REFRESH_MS = 5 * 60 * 1000; // auto-refresh every 5 minutes

interface Anomaly {
  anomaly_type: string;
  severity?: string;
  detail?: string;
}

// GET /api/kavasam/anomalies — the existing detail source (already returns
// exactly what a click-through needs: type, before/after numbers baked into
// the detail text, timestamp, and the linked CERT-In draft id if any).
interface KavasamAnomaly {
  snapshot_id: string;
  channel_id: string;
  channel_name: string;
  platform: string;
  anomaly_type: string;
  anomaly_detail: string;
  subscriber_count: number | null;
  video_count: number | null;
  scanned_at: string | null;
  cert_in_draft_id: string | null;
}

const ANOMALY_SEVERITY: Record<string, string> = {
  VIDEO_COUNT_DROP: 'CRITICAL',
  CHANNEL_STATUS_CHANGE: 'HIGH',
  SUBSCRIBER_DROP: 'HIGH',
  VIDEO_COUNT_SPIKE: 'LOW',
  SCAN_ERROR: 'MEDIUM',
};

const SEV_COLOR: Record<string, string> = {
  CRITICAL: 'var(--critical)',
  HIGH: 'var(--high)',
  MEDIUM: 'var(--medium)',
  LOW: 'var(--safe)',
};

function KavasamAnomalyModal({
  anomaly,
  onClose,
  onViewCertIn,
  certInAvailable,
}: {
  anomaly: KavasamAnomaly;
  onClose: () => void;
  onViewCertIn: () => void;
  certInAvailable: boolean;
}) {
  const severity = ANOMALY_SEVERITY[anomaly.anomaly_type] ?? 'MEDIUM';
  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      style={{
        position: 'fixed', inset: 0, zIndex: 50,
        background: 'rgba(0,0,0,0.9)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '24px', overflowY: 'auto',
      }}
    >
      <div style={{
        background: '#0a0a0a', border: '1px solid #1f1f1f',
        width: '100%', maxWidth: '560px', maxHeight: '90vh',
        overflowY: 'auto', position: 'relative', padding: '40px',
      }}>
        <button
          onClick={onClose}
          style={{
            position: 'absolute', top: '16px', right: '16px',
            background: 'none', border: 'none', color: '#555',
            fontSize: '20px', cursor: 'pointer', lineHeight: 1, padding: '4px 8px',
          }}
        >✕</button>

        <div style={{ marginBottom: '28px' }}>
          <div style={{ ...MONO, fontSize: '9px', color: '#444', letterSpacing: '3px', textTransform: 'uppercase', marginBottom: '8px' }}>
            KAVASAM ANOMALY
          </div>
          <div style={{ ...MONO, fontSize: '18px', color: SEV_COLOR[severity] ?? 'var(--accent)', letterSpacing: '1px' }}>
            {anomaly.channel_name}
          </div>
          <div style={{ width: '40px', height: '1px', background: SEV_COLOR[severity] ?? 'var(--accent)', marginTop: '12px' }} />
        </div>

        <Field label="Anomaly Type" value={anomaly.anomaly_type} accent />
        <Field label="Severity" value={severity} />
        <Field label="Detected At" value={anomaly.scanned_at ? fmtDetected(anomaly.scanned_at) : '—'} />
        <Field label="What Happened" value={anomaly.anomaly_detail || '—'} />

        <div style={{ borderTop: '1px solid #1a1a1a', paddingTop: '20px', marginTop: '4px' }}>
          <div style={{ display: 'flex', gap: '32px' }}>
            <div>
              <div style={LABEL_STYLE}>Video Count (current)</div>
              <div style={VALUE_STYLE}>{anomaly.video_count?.toLocaleString('en-IN') ?? '—'}</div>
            </div>
            <div>
              <div style={LABEL_STYLE}>Subscriber Count (current)</div>
              <div style={VALUE_STYLE}>{anomaly.subscriber_count?.toLocaleString('en-IN') ?? '—'}</div>
            </div>
          </div>
        </div>

        <div style={{ borderTop: '1px solid #1a1a1a', paddingTop: '20px', marginTop: '4px' }}>
          <div style={LABEL_STYLE}>CERT-In Draft</div>
          {anomaly.cert_in_draft_id ? (
            <button
              onClick={onViewCertIn}
              disabled={!certInAvailable}
              style={{
                marginTop: '8px', padding: '10px 18px',
                background: certInAvailable ? 'rgba(58,142,255,0.1)' : 'rgba(255,255,255,0.03)',
                border: `1px solid ${certInAvailable ? 'rgba(58,142,255,0.3)' : '#222'}`,
                color: certInAvailable ? 'var(--blue)' : '#444',
                ...MONO, fontSize: '11px', letterSpacing: '1px',
                cursor: certInAvailable ? 'pointer' : 'default',
              }}
            >
              {certInAvailable ? 'VIEW CERT-IN DRAFT →' : 'LOADING DRAFT…'}
            </button>
          ) : (
            <div style={{ ...VALUE_STYLE, color: '#555', marginTop: '8px' }}>
              No CERT-In draft — only CRITICAL anomalies trigger one.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

interface ChannelHealth {
  channel_name: string;
  subscriber_count: number | null;
  video_count: number | null;
  status: 'SECURE' | 'ANOMALY' | 'CRITICAL' | string;
  data_source: 'youtube_api' | 'public_scrape' | string;
  last_updated: string | null;
  anomalies: Anomaly[];
}

interface ChannelsResponse {
  channels: ChannelHealth[];
  overall_status: string;
  last_scan: string;
  api_configured: boolean;
}

interface MetricsResponse {
  channels_watched: number;
  anomalies_24h: number;
  cert_in_drafts: number;
  audit_retained_days: number;
  audit_retention_basis: string;
  data_source: string;
  api_configured: boolean;
  anomalies_by_severity: Record<string, number>;
}

function fmtCount(n: number | null): string {
  if (n === null || n === undefined) return '—';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString('en-IN');
}

const STATUS_COLOR: Record<string, string> = {
  SECURE: 'var(--safe)',
  ANOMALY: 'var(--high)',
  CRITICAL: 'var(--critical)',
};
const STATUS_ICON: Record<string, string> = {
  SECURE: '●',
  ANOMALY: '▲',
  CRITICAL: '✕',
};

function DataSourceBadge({ source }: { source: string }) {
  const isApi = source === 'youtube_api';
  return (
    <span style={{
      ...MONO, fontSize: '9px', letterSpacing: '1px', padding: '2px 6px', marginLeft: '8px',
      border: `1px solid ${isApi ? 'rgba(74,222,128,0.35)' : 'rgba(255,159,67,0.35)'}`,
      color: isApi ? 'var(--safe)' : 'var(--high)', textTransform: 'uppercase',
    }}>
      {isApi ? 'via YouTube API' : 'via public scrape'}
    </span>
  );
}

export default function KavasamPage() {
  const [channelsData, setChannelsData] = useState<ChannelsResponse | null>(null);
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);
  const [anomalies, setAnomalies] = useState<KavasamAnomaly[]>([]);
  const [certInDrafts, setCertInDrafts] = useState<CertInDraft[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedAnomaly, setSelectedAnomaly] = useState<KavasamAnomaly | null>(null);
  const [selectedDraft, setSelectedDraft] = useState<CertInDraft | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      // Reuses the existing /api/kavasam/anomalies (detail source) and
      // /api/certin/drafts (same endpoint the Findings page's CERT-In panel
      // already uses) rather than inventing new backend routes.
      const [cR, mR, aR, dR] = await Promise.allSettled([
        fetch(`${API_URL}/api/kavasam/channels`, { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null)),
        fetch(`${API_URL}/api/kavasam/metrics`, { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null)),
        fetch(`${API_URL}/api/kavasam/anomalies`, { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null)),
        fetch(`${API_URL}/api/certin/drafts`, { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null)),
      ]);
      if (cancelled) return;
      if (cR.status === 'fulfilled' && cR.value) setChannelsData(cR.value);
      if (mR.status === 'fulfilled' && mR.value) setMetrics(mR.value);
      if (aR.status === 'fulfilled' && aR.value) setAnomalies(aR.value.anomalies ?? []);
      if (dR.status === 'fulfilled' && dR.value) setCertInDrafts(dR.value.drafts ?? []);
      setLoading(false);
    }
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const channels = channelsData?.channels ?? [];
  // Real configured-channel count — was hardcoded as "Five" in the copy.
  const channelCount = metrics?.channels_watched ?? channels.length;

  function openAnomalyForChannel(channelName: string) {
    // Most recent anomaly row for this channel — /api/kavasam/anomalies is
    // already ordered newest-first.
    const match = anomalies.find((a) => a.channel_name === channelName);
    if (match) setSelectedAnomaly(match);
  }

  const draftForSelectedAnomaly = selectedAnomaly?.cert_in_draft_id
    ? certInDrafts.find((d) => d.id === selectedAnomaly.cert_in_draft_id) ?? null
    : null;

  return (
    <div className="page" id="page-kavasam" data-mc="kavasam">
      <div className="page-header">
        <h1 className="page-title">
          கவசம்
          <br />
          <span className="accent">The Shield.</span>
        </h1>
        <p className="page-sub">
          YouTube and social account protection. {channelCount} configured Broadcaster
          Network {channelCount === 1 ? 'channel' : 'channels'} monitored via the
          YouTube Data API with public-scrape fallback. CERT-In drafter produces
          reports for human review. Every action is written to the immutable
          audit trail.
        </p>
      </div>

      <div className="metrics">
        <div className="metric">
          <div className="metric-label">Channels Watched</div>
          <div className="metric-value accent">{metrics?.channels_watched ?? '—'}</div>
          <div className="metric-delta">
            {metrics
              ? metrics.api_configured ? 'via YouTube Data API' : 'via public scrape'
              : 'All Broadcaster properties'}
          </div>
        </div>
        <div className="metric">
          <div className="metric-label">Anomalies / 24h</div>
          <div className="metric-value" style={{ color: 'var(--high)' }}>
            {metrics?.anomalies_24h ?? '—'}
          </div>
          <div className="metric-delta">Statistical baseline</div>
        </div>
        <div className="metric">
          <div className="metric-label">CERT-In Drafts</div>
          <div className="metric-value">{metrics?.cert_in_drafts ?? '—'}</div>
          <div className="metric-delta">Ready to file</div>
        </div>
        <div className="metric">
          <div className="metric-label">Audit Log Retained</div>
          <div className="metric-value safe">
            {metrics ? `${metrics.audit_retained_days}d` : '—'}
          </div>
          <div className="metric-delta">Oldest entry · immutable</div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Multi-Agent Architecture</div>
          <div className="panel-tag">Python agents · run on demand</div>
        </div>
        <div className="panel-body" style={{ padding: '20px' }}>
          <div className="agents-grid cols-4" style={{ marginBottom: 0 }}>
            <div className="agent-cell module-color">
              <div className="agent-icon">⊙</div>
              <div className="agent-name">Channel Health</div>
              <div className="agent-status">YouTube API + Scrape</div>
            </div>
            <div className="agent-cell module-color">
              <div className="agent-icon">▲</div>
              <div className="agent-name">Anomaly Detect</div>
              <div className="agent-status">Statistical</div>
            </div>
            <div className="agent-cell module-color">
              <div className="agent-icon">◈</div>
              <div className="agent-name">CERT-In Drafter</div>
              <div className="agent-status">Drafts for review</div>
            </div>
            <div className="agent-cell module-color">
              <div className="agent-icon">⌬</div>
              <div className="agent-name">Compliance Log</div>
              <div className="agent-status">WORM audit trail</div>
            </div>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">
            Channel Health
            {channelsData && (
              <span style={{ ...MONO, fontSize: '10px', color: STATUS_COLOR[channelsData.overall_status] ?? 'var(--dim)', marginLeft: '10px', letterSpacing: '1px' }}>
                · {channelsData.overall_status}
              </span>
            )}
          </div>
          <div className="panel-tag">Auto-refresh · 5 min</div>
        </div>
        <div className="panel-body" style={{ padding: 0 }}>
          <div className="plugin-list">
            {loading && channels.length === 0 ? (
              <div className="plugin-row"><div className="plugin-name" style={{ color: 'var(--dim)' }}>Loading live channel data…</div></div>
            ) : channels.length === 0 ? (
              <div className="plugin-row"><div className="plugin-name" style={{ color: 'var(--dim)' }}>No channel data available.</div></div>
            ) : channels.map((ch) => (
              <div className="plugin-row" key={ch.channel_name}>
                <div className="plugin-name">
                  {ch.channel_name}{' '}
                  <span className="dim">
                    · {fmtCount(ch.subscriber_count)} subs · {ch.video_count?.toLocaleString('en-IN') ?? '—'} videos
                  </span>
                  <DataSourceBadge source={ch.data_source} />
                  {ch.anomalies.length > 0 && (
                    <button
                      onClick={() => openAnomalyForChannel(ch.channel_name)}
                      title="View anomaly details"
                      style={{
                        ...MONO, fontSize: '9px', letterSpacing: '1px', padding: '2px 6px', marginLeft: '8px',
                        border: '1px solid rgba(255,91,58,0.4)', background: 'rgba(255,91,58,0.1)', color: 'var(--accent)',
                        cursor: 'pointer',
                      }}
                    >
                      ⚠ {ch.anomalies[0].anomaly_type}
                    </button>
                  )}
                </div>
                <div style={{ ...MONO, fontSize: '11px', color: STATUS_COLOR[ch.status] ?? 'var(--dim)', letterSpacing: '1px', whiteSpace: 'nowrap' }}>
                  {STATUS_ICON[ch.status] ?? '●'} {ch.status}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Regulatory Coverage</div>
          <div className="panel-tag">India Compliance Map</div>
        </div>
        <div className="panel-body" style={{ padding: 0 }}>
          <table className="reg-table">
            <thead>
              <tr>
                <th>Regulation</th>
                <th>What Kavasam Covers</th>
                <th>Penalty if Missed</th>
              </tr>
            </thead>
            <tbody>
              <tr><td className="law">CERT-In 2022</td><td>6-hour auto-report + 180-day logs</td><td className="penalty">Up to ₹1 Crore</td></tr>
              <tr><td className="law">DPDP Act 2023</td><td>Breach detection + tamper-proof audit trail</td><td className="penalty">Up to ₹250 Crore</td></tr>
              <tr><td className="law">IT Act 2000 S.43A</td><td>Documented reasonable security practices</td><td className="penalty">Unlimited</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      {selectedAnomaly && (
        <KavasamAnomalyModal
          anomaly={selectedAnomaly}
          onClose={() => setSelectedAnomaly(null)}
          onViewCertIn={() => { if (draftForSelectedAnomaly) setSelectedDraft(draftForSelectedAnomaly); }}
          certInAvailable={!!draftForSelectedAnomaly}
        />
      )}
      {selectedDraft && (
        <CertInReportModal draft={selectedDraft} onClose={() => setSelectedDraft(null)} />
      )}
    </div>
  );
}

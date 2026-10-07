'use client';

import { useEffect, useRef, useState } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
const WS_URL = `${API_URL.replace(/^http/, 'ws')}/ws/live-feed`;
const MAX_FEED = 100;

const AGENT_ICONS: Record<string, string> = {
  kaaval:  '⊙',
  kavasam: '⚡',
  veriff:  '▲',
  judge:   '◈',
  certin:  '◐',
  audit:   '∞',
};

interface AgentStatus {
  name: string;
  module: string;
  status: string;
  last_run: string | null;
  threats_today?: number;
  anomalies_today?: number;
  verdicts_today?: number;
  validations_today?: number;
  drafts_ready?: number;
  events_today?: number;
}

interface ScanStatus {
  scan_id: string;
  status: string;
  agents: AgentStatus[];
  kill_switch_active: boolean;
}

// ── WebSocket message shapes ─────────────────────────────
interface WsThreat {
  type: 'threat';
  id?: string;
  threat_type: string;
  severity: string;
  title: string;
  detected_at?: string | null;
  source?: string;
}
interface WsScanEvent {
  type: 'scan_event';
  event: 'scan_started' | 'scan_completed' | string;
  data: {
    scan_id?: string;
    target?: string;
    scan_type?: string;
    findings_count?: number;
    status?: string;
    error_message?: string | null;
  };
  timestamp: string;
}
interface WsPing { type: 'ping' }
type WsMessage = WsThreat | WsScanEvent | WsPing;

type ConnState = 'connecting' | 'live' | 'disconnected';
type Sev = 'critical' | 'high' | 'medium' | 'info';

interface FeedItem {
  key: string;
  ts: string;
  sev: Sev;
  sevLabel: string;
  threatType: string;
  title: string;
  isHistorical: boolean;
}

interface ScanProgress {
  scanId: string;
  status: 'running' | 'completed' | 'failed';
  findings: number;
  target?: string;
  errorMessage?: string | null;
}

function sevClass(severity: string): Sev {
  const s = (severity || '').toUpperCase();
  if (s === 'CRITICAL') return 'critical';
  if (s === 'HIGH') return 'high';
  if (s === 'MEDIUM') return 'medium';
  return 'info'; // LOW / INFO / unknown
}

function fmtClock(iso?: string | null): string {
  const d = iso ? new Date(iso) : new Date();
  const t = isNaN(d.getTime()) ? new Date() : d;
  return [t.getHours(), t.getMinutes(), t.getSeconds()]
    .map((n) => String(n).padStart(2, '0'))
    .join(':');
}

function formatRuntime(totalSeconds: number) {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  return [h, m, s].map((n) => String(n).padStart(2, '0')).join(':');
}

function fmtLastRun(iso: string | null): string {
  if (!iso) return 'Never';
  const d = new Date(iso);
  const diffMin = Math.floor((Date.now() - d.getTime()) / 60000);
  if (diffMin < 1) return 'Just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH}h ago`;
  return new Intl.DateTimeFormat('en-IN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).format(d);
}

function agentMetric(a: AgentStatus): string {
  if (a.threats_today      != null) return `${a.threats_today} threats today`;
  if (a.anomalies_today    != null) return `${a.anomalies_today} anomalies today`;
  if (a.verdicts_today     != null) return `${a.verdicts_today} verdicts today`;
  if (a.validations_today  != null) return `${a.validations_today} validated today`;
  if (a.drafts_ready       != null) return `${a.drafts_ready} drafts ready`;
  if (a.events_today       != null) return `${a.events_today} events today`;
  return a.status;
}

const STATUS_DOT: Record<string, string> = {
  ACTIVE:  'var(--accent)',
  RECENT:  '#555',
  IDLE:    '#333',
  NO_DATA: '#2a2a2a',
};

const CONN_META: Record<ConnState, { label: string; color: string }> = {
  live:         { label: '● LIVE',            color: 'var(--safe)' },
  connecting:   { label: '○ CONNECTING…',     color: 'var(--high)' },
  disconnected: { label: '✕ DISCONNECTED',    color: 'var(--critical)' },
};

function ColHeader({ label, isLive }: { label: string; isLive: boolean }) {
  return (
    <div style={{
      fontFamily: "'Space Mono', monospace",
      fontSize: '9px',
      letterSpacing: '2px',
      color: isLive ? 'var(--safe)' : '#3a3a3a',
      padding: '10px 14px 8px',
      borderBottom: '1px solid var(--line)',
      display: 'flex',
      alignItems: 'center',
      gap: '7px',
      flexShrink: 0,
    }}>
      {isLive && (
        <span style={{
          display: 'inline-block',
          width: '6px', height: '6px',
          borderRadius: '50%',
          background: 'var(--accent)',
          boxShadow: '0 0 6px var(--accent)',
        }} />
      )}
      {label}
    </div>
  );
}

export default function LiveOperationsPage() {
  const [conn, setConn] = useState<ConnState>('connecting');
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [eventsReceived, setEventsReceived] = useState(0);
  const [scan, setScan] = useState<ScanProgress | null>(null);
  const [progress, setProgress] = useState(0);
  const [runtime, setRuntime] = useState(0);
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null);

  const liveColRef = useRef<HTMLDivElement>(null); // right column — scrolls to top on new live events
  const seqRef = useRef(0);
  const eventsCountRef = useRef(0); // counts events per WS session to identify historical backfill

  // ── WebSocket connection with auto-reconnect ────────────
  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    function pushThreat(m: WsThreat) {
      seqRef.current += 1;
      eventsCountRef.current += 1;
      // First 20 events per WS session are treated as historical backfill
      const isHistorical = eventsCountRef.current <= 20;
      const item: FeedItem = {
        key: `${m.id ?? 'ev'}-${seqRef.current}`,
        ts: fmtClock(m.detected_at),
        sev: sevClass(m.severity),
        sevLabel: (m.severity || 'INFO').toUpperCase(),
        threatType: m.threat_type || 'EVENT',
        title: m.title || '',
        isHistorical,
      };
      setFeed((prev) => [item, ...prev].slice(0, MAX_FEED));
      setEventsReceived((n) => n + 1);
    }

    function connect() {
      setConn('connecting');
      try {
        ws = new WebSocket(WS_URL);
      } catch {
        scheduleReconnect();
        return;
      }

      ws.onopen = () => {
        setConn('live');
        eventsCountRef.current = 0; // reset backfill counter on each (re)connect
      };

      ws.onmessage = (evt) => {
        let msg: WsMessage;
        try {
          msg = JSON.parse(evt.data);
        } catch {
          return;
        }
        if (msg.type === 'threat') {
          pushThreat(msg);
        } else if (msg.type === 'scan_event') {
          if (msg.event === 'scan_started') {
            setScan({ scanId: msg.data.scan_id ?? '—', status: 'running', findings: 0, target: msg.data.target });
            setProgress(6);
            setRuntime(0);
          } else if (msg.event === 'scan_completed') {
            const failed = msg.data.status === 'failed';
            const nextStatus: 'completed' | 'failed' = failed ? 'failed' : 'completed';
            setScan((prev) =>
              prev
                ? {
                    ...prev,
                    status: nextStatus,
                    findings: msg.data.findings_count ?? prev.findings,
                    errorMessage: msg.data.error_message ?? null,
                  }
                : {
                    scanId: msg.data.scan_id ?? '—',
                    status: nextStatus,
                    findings: msg.data.findings_count ?? 0,
                    errorMessage: msg.data.error_message ?? null,
                  },
            );
            setProgress(100);
          }
        }
        // ping → keep-alive, ignore
      };

      ws.onclose = () => {
        if (!closed) { setConn('disconnected'); scheduleReconnect(); }
      };
      ws.onerror = () => { try { ws?.close(); } catch { /* noop */ } };
    }

    function scheduleReconnect() {
      if (closed) return;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connect, 3000); // auto-reconnect after 3s
    }

    connect();
    return () => {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      try { ws?.close(); } catch { /* noop */ }
    };
  }, []);

  // ── auto-scroll: keep live column showing newest event at top ──
  useEffect(() => {
    if (liveColRef.current) {
      liveColRef.current.scrollTop = 0;
    }
  }, [feed]);

  // ── runtime + progress ticker while a scan runs ─────────
  useEffect(() => {
    if (!scan || scan.status !== 'running') return;
    const id = setInterval(() => {
      setRuntime((s) => s + 1);
      setProgress((p) => (p < 92 ? p + Math.max(1, Math.round((92 - p) * 0.08)) : p));
    }, 1000);
    return () => clearInterval(id);
  }, [scan]);

  // ── agents grid (unchanged status poll) ─────────────────
  useEffect(() => {
    function fetchStatus() {
      fetch(`${API_URL}/api/scan/status`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => { if (d) setScanStatus(d); })
        .catch(() => {});
    }
    fetchStatus();
    const id = setInterval(fetchStatus, 30000);
    return () => clearInterval(id);
  }, []);

  const agents = scanStatus?.agents ?? null;
  const killActive = scanStatus?.kill_switch_active ?? false;
  const activeCount = agents ? agents.filter((a) => a.status === 'ACTIVE').length : 6;
  const connMeta = CONN_META[conn];

  return (
    <div className="page" id="page-livescan">
      {killActive && (
        <div style={{
          background: 'rgba(255,56,56,0.1)', border: '1px solid rgba(255,56,56,0.4)',
          color: '#ff3838', fontFamily: "'Space Mono', monospace", fontSize: '12px',
          letterSpacing: '1px', padding: '12px 20px', marginBottom: '24px',
          display: 'flex', alignItems: 'center', gap: '10px',
        }}>
          ⏸ ALL AGENTS PAUSED — Kill switch active
        </div>
      )}

      <div className="page-header">
        <div className="page-label">LIVE OPERATIONS</div>
        <h1 className="page-title">
          Scan in progress.
          <br />
          <span className="accent">{activeCount} agents engaged.</span>
        </h1>
        <p className="page-sub">
          Real-time view of the multi-agent system. Findings stream over a live
          WebSocket as each agent confirms them — no polling, no refresh.
        </p>
      </div>

      <div className="scan-progress">
        <div className="scan-progress-header">
          <div>
            <div className="scan-target">
              Target · {scan?.target ? scan.target.replace(/_/g, ' ') : 'Broadcaster Network · Tamil Web Surface'}
            </div>
            <div className="scan-title">
              {scan ? `SCAN #${scan.scanId.slice(0, 8).toUpperCase()}` : 'IDLE'} ·{' '}
              {scan?.status === 'running'
                ? 'Scanning…'
                : scan?.status === 'completed'
                ? 'Complete'
                : scan?.status === 'failed'
                ? 'FAILED'
                : 'Awaiting scan'}
            </div>
            <div className="scan-sub">
              {scan?.status === 'completed'
                ? `${scan.findings} findings streamed`
                : scan?.status === 'failed'
                ? scan.errorMessage ?? 'Scan failed — see logs for details'
                : 'Multi-agent sweep · findings stream live below'}
            </div>
          </div>
          <div className="scan-runtime">
            <div className="scan-runtime-label">Runtime</div>
            <div className="scan-runtime-value">{formatRuntime(runtime)}</div>
          </div>
        </div>
        <div className="progress-bar">
          <div
            className="progress-fill"
            style={{
              width: `${progress}%`,
              transition: 'width 0.6s ease',
              background:
                scan?.status === 'completed'
                  ? 'var(--safe)'
                  : scan?.status === 'failed'
                  ? 'var(--critical)'
                  : undefined,
            }}
          />
        </div>
        <div className="progress-meta">
          <span>
            {scan
              ? scan.status === 'completed'
                ? 'Scan complete'
                : scan.status === 'failed'
                ? 'Scan failed'
                : 'Scan running'
              : 'No active scan'}
          </span>
          <span>{scan ? `${scan.findings} findings` : `${eventsReceived} events`}</span>
          <span>{progress}%</span>
        </div>
      </div>

      <div className="agents-grid">
        {agents ? (
          agents.map((a) => (
            <div className="agent-cell" key={a.module}>
              <div className="agent-icon" style={{ color: STATUS_DOT[a.status] ?? '#333' }}>
                {AGENT_ICONS[a.module] ?? '●'}
              </div>
              <div className="agent-name">{a.name}</div>
              <div className="agent-status" style={{ color: a.status === 'ACTIVE' ? 'var(--safe)' : '#555' }}>
                {agentMetric(a)}
              </div>
              <div style={{ fontFamily: "'Space Mono', monospace", fontSize: '9px', color: '#444', marginTop: '4px', letterSpacing: '0.5px' }}>
                {fmtLastRun(a.last_run)}
              </div>
            </div>
          ))
        ) : (
          ['Kaaval AI', 'Digital Kavasam', 'Veriff', 'Judge LLM', 'CERT-In Drafter', 'Audit Logger'].map((name) => (
            <div className="agent-cell active" key={name}>
              <div className="agent-icon">●</div>
              <div className="agent-name">{name}</div>
              <div className="agent-status" style={{ color: '#333' }}>Loading…</div>
            </div>
          ))
        )}
      </div>

      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Live Probe Feed</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
            <span style={{
              fontFamily: "'Space Mono', monospace", fontSize: '11px',
              color: connMeta.color, letterSpacing: '1px',
            }}>
              {connMeta.label}
            </span>
            <span style={{ fontFamily: "'Space Mono', monospace", fontSize: '11px', color: '#555', letterSpacing: '0.5px' }}>
              {conn === 'live' ? `Connected · ${eventsReceived} events received` : `${eventsReceived} events received`}
            </span>
          </div>
        </div>

        {/* ── Two-column feed ── */}
        <div style={{ display: 'flex', flexDirection: 'row', height: '400px' }}>

          {/* LEFT — historical backfill */}
          <div style={{ width: '50%', display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--line)' }}>
            <ColHeader
              label={`HISTORICAL · Last ${feed.filter((f) => f.isHistorical).length} events`}
              isLive={false}
            />
            <div style={{ flex: 1, overflowY: 'auto', padding: '4px 0' }}>
              {feed.filter((f) => f.isHistorical).length === 0 ? (
                <div className="feed-row" style={{ opacity: 0.35 }}>
                  <span className="feed-ts">--:--:--</span>
                  <span className="feed-sev info">—</span>
                  <span className="feed-msg">No historical events</span>
                </div>
              ) : (
                feed.filter((f) => f.isHistorical).map((f) => (
                  <div className="feed-row" key={f.key} style={{ opacity: 0.6 }}>
                    <span className="feed-ts" style={{ color: '#444' }}>{f.ts}</span>
                    <span className={`feed-sev ${f.sev}`} style={{ opacity: 0.6 }}>{f.sevLabel}</span>
                    <span className="feed-msg" style={{ color: '#555', fontSize: '11px' }}>
                      <span>{f.threatType}</span>
                      {f.title ? <> · {f.title}</> : null}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* RIGHT — live stream */}
          <div style={{ width: '50%', display: 'flex', flexDirection: 'column' }}>
            <ColHeader label="LIVE · This session" isLive />
            <div ref={liveColRef} style={{ flex: 1, overflowY: 'auto', padding: '4px 0' }}>
              {feed.filter((f) => !f.isHistorical).length === 0 ? (
                <div className="feed-row">
                  <span className="feed-ts">--:--:--</span>
                  <span className="feed-sev info">WAIT</span>
                  <span className="feed-msg" style={{ color: 'var(--dim)' }}>
                    {conn === 'live'
                      ? 'Waiting for new events…'
                      : 'Connecting to live feed…'}
                  </span>
                </div>
              ) : (
                feed.filter((f) => !f.isHistorical).map((f) => (
                  <div className="feed-row" key={f.key}>
                    <span className="feed-ts">{f.ts}</span>
                    <span className={`feed-sev ${f.sev}`}>{f.sevLabel}</span>
                    <span className="feed-msg">
                      <span className="dim">{f.threatType}</span>
                      {f.title ? <> · {f.title}</> : null}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}

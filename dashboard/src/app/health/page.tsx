'use client';

import { useEffect, useState } from 'react';
import type { CSSProperties } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

// ── Types ──────────────────────────────────────────────────────────────────

interface HealthData {
  modules: {
    kaaval: {
      status: string;
      threats_24h: number | null;
      checks: { ollama?: string; ollama_ms?: number | null; database?: string; agents?: string };
    };
    kavasam: {
      status: string;
      scans_24h: number | null;
      snapshots_24h: number | null;
      checks: { database?: string; scans?: string };
    };
    veriff: {
      status: string;
      checks_24h: number | null;
      checks: { database?: string; agents?: string; scrapers?: string; verdict_engine?: string };
    };
    ollama:   { status: string; response_time_ms: number | null };
    database: { status: string; response_time_ms: number | null };
  };
  overall: string;
  checked_at: string;
}

/** Per-agent last-run timestamps — the only historical signal the platform records. */
interface AgentStatus {
  name: string;
  module: string;
  status: string;
  last_run: string | null;
}

interface ScanStatus {
  agents: AgentStatus[];
}

// ── Helpers ────────────────────────────────────────────────────────────────

const STATUS_LABEL: Record<string, string> = {
  HEALTHY:  '● HEALTHY',
  DEGRADED: '● DEGRADED',
  DOWN:     '● DOWN',
};

const STATUS_CLASS: Record<string, string> = {
  HEALTHY:  'safe',
  DEGRADED: 'medium',
  DOWN:     'critical',
};

function statusLabel(s: string | undefined) { return STATUS_LABEL[s ?? ''] ?? '● —'; }
function statusClass(s: string | undefined) { return STATUS_CLASS[s ?? ''] ?? ''; }

function fmtTs(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-IN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}

/** Relative age of a real timestamp — no rounding to flattering values. */
function fmtRelative(iso: string): string {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1)    return 'just now';
  if (mins < 60)   return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48)    return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

// ── Original components ────────────────────────────────────────────────────

interface SlaRow {
  label: string;
  value: string;
}

function SlaCard({
  title,
  mc,
  status,
  rows,
  style,
}: {
  title: string;
  mc: 'kaaval' | 'kavasam' | 'veriff';
  status: string;
  rows: SlaRow[];
  style?: CSSProperties;
}) {
  return (
    <div className="sla-card" style={style}>
      <div className="sla-head">
        <div className={`sla-title ${mc}`}>{title}</div>
        <div className={`sla-status ${statusClass(status)}`}>{statusLabel(status)}</div>
      </div>
      <div className="page-label" style={{ marginBottom: '10px' }}>
        LIVE CHECK
      </div>
      {rows.map((r) => (
        <div className="sla-row" key={r.label}>
          <div>{r.label}</div>
          <div className="val">{r.value}</div>
        </div>
      ))}
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function HealthPage() {
  const [data, setData] = useState<HealthData | null>(null);
  const [scan, setScan] = useState<ScanStatus | null>(null);

  useEffect(() => {
    async function fetch_() {
      try {
        const res = await fetch(`${API_URL}/api/health/modules`, { cache: 'no-store' });
        if (res.ok) setData(await res.json());
      } catch { /* keep stale */ }
      try {
        const res = await fetch(`${API_URL}/api/scan/status`, { cache: 'no-store' });
        if (res.ok) setScan(await res.json());
      } catch { /* keep stale */ }
    }
    fetch_();
    const t = setInterval(fetch_, 60_000);
    return () => clearInterval(t);
  }, []);

  const m = data?.modules;

  /** Real last-run time for a module, from the agent status feed. */
  function lastRun(moduleKey: string): string {
    const agent = scan?.agents.find((a) => a.module === moduleKey);
    if (!agent?.last_run) return 'Never run';
    return fmtRelative(agent.last_run);
  }

  // Real values — fall back gracefully before first fetch
  const kaavalStatus  = m?.kaaval.status;
  const kavasamStatus = m?.kavasam.status;
  const veriffStatus  = m?.veriff.status;
  const threats24h    = m?.kaaval.threats_24h ?? '…';
  const checks24h     = m?.veriff.checks_24h  ?? '…';
  const scans24h      = m?.kavasam.scans_24h  ?? '…';
  const snapshots24h  = m?.kavasam.snapshots_24h ?? '…';
  const ollamaMs      = m?.ollama.response_time_ms != null  ? `${m.ollama.response_time_ms}ms`   : '…';
  const dbMs          = m?.database.response_time_ms != null ? `${m.database.response_time_ms}ms` : '…';
  const checkedAt     = data?.checked_at ? fmtTs(data.checked_at) : '…';
  const overall       = data?.overall ?? '…';

  return (
    <div className="page" id="page-health">
      <div className="page-header">
        <div className="page-label">MODULE HEALTH · LIVE STATUS</div>
        <h1 className="page-title med">
          What is running,
          <br />
          <span className="accent">checked right now.</span>
        </h1>
        <p className="page-sub">
          Every value on this page is read live from the platform at page load.
          Uptime history and incident history are <strong>not tracked yet</strong>,
          so they are not shown — this is a point-in-time status check, not an
          SLA report.
        </p>
      </div>

      <div className="metrics">
        <div className="metric">
          <div className="metric-label">Overall Status</div>
          <div className={`metric-value ${statusClass(overall)}`} style={{ fontSize: '20px' }}>
            {overall}
          </div>
          <div className="metric-delta">Worst module status</div>
        </div>
        <div className="metric">
          <div className="metric-label">Database Response</div>
          <div className="metric-value accent">{dbMs}</div>
          <div className={`metric-delta ${statusClass(m?.database.status)}`}>
            PostgreSQL
          </div>
        </div>
        <div className="metric">
          <div className="metric-label">LLM Response</div>
          <div className="metric-value accent">{ollamaMs}</div>
          <div className={`metric-delta ${statusClass(m?.ollama.status)}`}>
            Ollama · qwen2.5:14b
          </div>
        </div>
        <div className="metric">
          <div className="metric-label">Last Checked</div>
          <div className="metric-value safe" style={{ fontSize: '16px' }}>{checkedAt}</div>
          <div className="metric-delta">Re-checks every 60s</div>
        </div>
      </div>

      <div className="security-grid">
        <SlaCard
          title="KAAVAL AI"
          mc="kaaval"
          status={kaavalStatus ?? '…'}
          rows={[
            { label: 'Ollama response time',    value: ollamaMs },
            { label: 'Database',                value: m?.kaaval.checks.database === 'OK' ? 'OK' : '…' },
            { label: 'Agent files present',     value: m?.kaaval.checks.agents ?? '…' },
            { label: 'Last run',                value: lastRun('kaaval') },
            { label: 'Threats detected · 24h',  value: String(threats24h) },
          ]}
        />
        <SlaCard
          title="DIGITAL KAVASAM"
          mc="kavasam"
          status={kavasamStatus ?? '…'}
          rows={[
            { label: 'Database',               value: m?.kavasam.checks?.database === 'OK' ? 'OK' : '…' },
            { label: 'Recent scans',           value: m?.kavasam.checks?.scans ?? '…' },
            { label: 'Last run',               value: lastRun('kavasam') },
            { label: 'Scans completed · 24h',  value: String(scans24h) },
            { label: 'Snapshots · 24h',        value: String(snapshots24h) },
          ]}
        />
      </div>

      <SlaCard
        title="VERIFF TAMIL"
        mc="veriff"
        status={veriffStatus ?? '…'}
        rows={[
          { label: 'Database',          value: m?.veriff.checks?.database === 'OK' ? 'OK' : '…' },
          { label: 'Scrapers',          value: m?.veriff.checks?.scrapers ?? '…' },
          { label: 'Verdict engine',    value: m?.veriff.checks?.verdict_engine ?? '…' },
          { label: 'Last verdict',      value: lastRun('veriff') },
          { label: 'Fact checks · 24h', value: String(checks24h) },
        ]}
        style={{ marginBottom: '24px' }}
      />

      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Infrastructure · Live</div>
          <div className="panel-tag">DB + Ollama response times</div>
        </div>
        <div className="panel-body" style={{ padding: '0 22px' }}>
          <div className="activity-item">
            <div className="activity-time">DB</div>
            <div className={`activity-tag ${statusClass(m?.database.status)}`}>
              {statusLabel(m?.database.status)}
            </div>
            <div className="activity-text">
              PostgreSQL · response time: {dbMs}
            </div>
          </div>
          <div className="activity-item">
            <div className="activity-time">LLM</div>
            <div className={`activity-tag ${statusClass(m?.ollama.status)}`}>
              {statusLabel(m?.ollama.status)}
            </div>
            <div className="activity-text">
              Ollama · qwen2.5:14b · response time: {ollamaMs}
            </div>
          </div>
        </div>
      </div>

      <div className="panel" style={{ marginTop: '24px' }}>
        <div className="panel-header">
          <div className="panel-title">Uptime &amp; Incident History</div>
          <div className="panel-tag">Not tracked</div>
        </div>
        <div className="panel-body" style={{ padding: '20px 22px' }}>
          <div style={{ fontSize: '12px', color: '#888', lineHeight: 1.7 }}>
            Sentinel does not record uptime samples or module incidents yet, so
            there is no history to show. Adding it needs a probe that writes a
            status sample on a schedule and an incident record when a module
            changes state — neither exists today.
            <br /><br />
            Until then this page reports only the live check above. Agent
            &ldquo;last run&rdquo; times come from the most recent record each
            module wrote to the database, which is the one piece of history the
            platform genuinely keeps.
          </div>
        </div>
      </div>
    </div>
  );
}

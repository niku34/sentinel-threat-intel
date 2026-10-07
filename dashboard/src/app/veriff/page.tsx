import { VeriffVerifyPanel } from '@/components/veriff-verify-panel';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

interface Metrics {
  checks_today: number;
  avg_verdict_time: number;
  stories_held: number;
  audit_trail: string;
}

async function getMetrics(): Promise<Metrics> {
  try {
    const res = await fetch(`${API_URL}/api/veriff/metrics`, { cache: 'no-store' });
    if (!res.ok) throw new Error(`API error ${res.status}`);
    return res.json();
  } catch {
    return { checks_today: 0, avg_verdict_time: 0, stories_held: 0, audit_trail: '0%' };
  }
}

export default async function VeriffPage() {
  const metrics = await getMetrics();

  return (
    <div className="page" id="page-veriff" data-mc="veriff">
      <div className="page-header">
        <h1 className="page-title">
          நேர்மை
          <br />
          <span className="accent">Truth.</span>
        </h1>
        <p className="page-sub">
          Real-time fact-check assistant for the Example News newsroom. Tamil claim
          extraction, reverse media forensics, 7-database cross-reference. Verdict in
          under 30 seconds.
        </p>
      </div>

      <VeriffVerifyPanel />

      <div className="metrics">
        <div className="metric">
          <div className="metric-label">Checks Today</div>
          <div className="metric-value accent">{metrics.checks_today}</div>
          <div className="metric-delta up">14 journalists active</div>
        </div>
        <div className="metric">
          <div className="metric-label">Avg Verdict Time</div>
          <div className="metric-value safe">{metrics.avg_verdict_time}s</div>
          <div className="metric-delta">Target: under 30s</div>
        </div>
        <div className="metric">
          <div className="metric-label">Stories Held</div>
          <div className="metric-value" style={{ color: 'var(--high)' }}>
            {metrics.stories_held}
          </div>
          <div className="metric-delta">Defamation prevented</div>
        </div>
        <div className="metric">
          <div className="metric-label">Audit Trail</div>
          <div className="metric-value safe">{metrics.audit_trail}</div>
          <div className="metric-delta safe">DPDP compliant</div>
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
              <div className="agent-name">Media Analysis</div>
              <div className="agent-status">pHash + PIL forensics</div>
            </div>
            <div className="agent-cell module-color">
              <div className="agent-icon">▲</div>
              <div className="agent-name">Claim Extraction</div>
              <div className="agent-status">Tamil NLP</div>
            </div>
            <div className="agent-cell module-color">
              <div className="agent-icon">◈</div>
              <div className="agent-name">Fact-Check DB</div>
              <div className="agent-status">7 Sources</div>
            </div>
            <div className="agent-cell module-color">
              <div className="agent-icon">⌬</div>
              <div className="agent-name">Source Credibility</div>
              <div className="agent-status">Domain Rep</div>
            </div>
            <div className="agent-cell module-color">
              <div className="agent-icon">∞</div>
              <div className="agent-name">Verdict Engine</div>
              <div className="agent-status">Aggregator</div>
            </div>
          </div>
        </div>
      </div>

      <div className="legal-callout">
        <div className="label">Legal Protection Value</div>
        <p>
          Every fact-check logged by Veriff is evidence your journalists
          performed due diligence before broadcast.
        </p>
        <p style={{ marginTop: '14px' }}>
          In a defamation case or MIB content notice, this audit trail is the
          difference between
        </p>
        <p style={{ marginTop: '8px' }}>
          <span className="quote-bad">&quot;we ran it without checking&quot;</span>
        </p>
        <p style={{ marginTop: '4px', color: 'var(--text-mid)' }}>and</p>
        <p>
          <span className="quote">
            &quot;we verified it through seven independent sources before going to
            air.&quot;
          </span>
        </p>
      </div>
    </div>
  );
}

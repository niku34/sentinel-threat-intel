'use client';

import { useEffect, useState } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
const MONO: React.CSSProperties = { fontFamily: "'Space Mono', monospace" };

type DataSource = 'measured' | 'estimated';

/** Every numeric metric arrives tagged with how it was produced. */
interface Metric<T = number> {
  value: T | null;
  data_source: DataSource;
  note: string | null;
}

const EST_COLOR = 'var(--medium)';

interface KaavalAccuracy {
  total_threats: Metric;
  confirmed_threats: Metric;
  dismissed_threats: Metric;
  threats_by_type: Metric<Record<string, number>>;
  false_positive_rate: Metric;
  precision_low: Metric;
  precision_high: Metric;
  precision_label: string;
  features: Record<string, Metric>;
  note: string;
}

interface KavasAmAccuracy {
  channels_monitored: Metric;
  total_anomalies: Metric;
  cert_in_drafts: Metric;
  detection_rate: Metric;
  false_positive_rate_label: Metric<string>;
  strongest_feature: string;
  features: Record<string, Metric>;
  note: string;
}

interface VeriffAccuracy {
  total_checks: Metric;
  verdict_breakdown: Metric<Record<string, number>>;
  avg_verdict_time_seconds: Metric;
  precision_low: Metric;
  precision_high: Metric;
  precision_label: string;
  strongest_source: string;
  weakest_area: string;
  features: Record<string, Metric>;
  note: string;
}

interface Overall {
  platform_reliability: Metric;
  most_accurate_module: string;
  least_accurate_module: string;
  accuracy_improves_with: string;
  next_milestone: string;
}

interface AccuracyData {
  legend: Record<DataSource, string>;
  kaaval: KaavalAccuracy;
  kavasam: KavasAmAccuracy;
  veriff: VeriffAccuracy;
  overall: Overall;
}

interface KaavalTrendPoint {
  day: string;
  avg_confidence: number;
  count: number;
}

interface VeriffTrendPoint {
  day: string;
  checks: number;
  avg_confidence: number;
}

interface OverallTrendPoint {
  day: string;
  confidence: number;
}

interface TrendData {
  kaaval_trend: KaavalTrendPoint[];
  veriff_trend: VeriffTrendPoint[];
  overall_trend: OverallTrendPoint[];
}

const FEATURE_LABELS: Record<string, string> = {
  fake_page_detection: 'Fake page detection',
  brigade_detection: 'Brigade detection',
  deepfake_detection: 'Deepfake detection',
  tamil_nlp_classification: 'Tamil NLP classification',
  video_deletion_detection: 'Video deletion detection',
  unknown_admin_detection: 'Unknown admin detection',
  cert_in_report_accuracy: 'CERT-In report accuracy',
  anomaly_baselining: 'Anomaly baselining',
  reverse_image_search: 'Reverse image search (image only)',
  fact_check_db_matching: 'Fact-check DB matching',
  tamil_claim_extraction: 'Tamil claim extraction',
  celebrity_entertainment: 'Celebrity / entertainment',
};

function ProgressBar({ value, color, max = 100 }: { value: number; color: string; max?: number }) {
  const pct = Math.min(100, Math.round((value / max) * 100));
  return (
    <div style={{
      height: '4px', background: 'var(--line)', borderRadius: '2px',
      position: 'relative', overflow: 'hidden',
    }}>
      <div style={{
        position: 'absolute', left: 0, top: 0, height: '100%',
        width: `${pct}%`, background: color,
        transition: 'width 0.6s ease',
      }} />
    </div>
  );
}

/** Small badge that makes an estimate impossible to mistake for a measurement. */
function SourceBadge({ source }: { source: DataSource }) {
  const measured = source === 'measured';
  return (
    <span style={{
      ...MONO, fontSize: '8px', letterSpacing: '1px', marginLeft: '6px',
      padding: '1px 5px',
      border: `1px solid ${measured ? 'rgba(74,222,128,0.35)' : 'rgba(255,176,32,0.4)'}`,
      color: measured ? 'var(--safe)' : EST_COLOR,
      whiteSpace: 'nowrap',
    }}>
      {measured ? 'MEASURED' : 'ESTIMATED'}
    </span>
  );
}

function FeatureRow({ label, value, source }: { label: string; value: number; source: DataSource }) {
  // Estimates are drawn in the estimate colour and dimmed — never the same as measured.
  const color = source === 'measured' ? 'var(--safe)' : EST_COLOR;
  return (
    <div style={{ marginBottom: '10px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px', gap: '8px' }}>
        <span style={{ ...MONO, fontSize: '11px', color: '#aaa' }}>{label}</span>
        <span style={{ ...MONO, fontSize: '11px', color, fontWeight: 700 }}>
          {source === 'estimated' ? '~' : ''}{value}%
          <SourceBadge source={source} />
        </span>
      </div>
      <div style={{ opacity: source === 'estimated' ? 0.55 : 1 }}>
        <ProgressBar value={value} color={color} />
      </div>
    </div>
  );
}

function ModuleCard({
  title,
  subtitle,
  accentColor,
  overallLabel,
  overallValue,
  overallSource,
  barColor,
  features,
  statsRows,
  note,
}: {
  title: string;
  subtitle: string;
  accentColor: string;
  overallLabel: string;
  overallValue: string;
  overallSource: DataSource;
  barColor: string;
  features: { label: string; value: number; source: DataSource }[];
  statsRows: { label: string; value: string; source: DataSource }[];
  note: string;
}) {
  return (
    <div style={{
      border: `1px solid ${accentColor}33`,
      background: 'var(--bg)',
      marginBottom: '20px',
    }}>
      {/* Card header */}
      <div style={{
        borderBottom: `1px solid ${accentColor}33`,
        padding: '18px 22px',
        display: 'flex', alignItems: 'baseline', gap: '12px',
      }}>
        <div style={{ ...MONO, fontSize: '13px', color: accentColor, letterSpacing: '2px', fontWeight: 700 }}>
          {title}
        </div>
        <div style={{ ...MONO, fontSize: '10px', color: '#555', letterSpacing: '1px' }}>
          {subtitle}
        </div>
      </div>

      <div style={{ padding: '22px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '28px' }}>
        {/* Left: overall + feature breakdown */}
        <div>
          <div style={{ marginBottom: '18px' }}>
            <div style={{ ...MONO, fontSize: '9px', color: '#555', letterSpacing: '2px', marginBottom: '6px' }}>
              OVERALL ACCURACY
              <SourceBadge source={overallSource} />
            </div>
            <div style={{
              ...MONO, fontSize: '28px',
              color: overallSource === 'estimated' ? EST_COLOR : barColor,
              letterSpacing: '-1px', marginBottom: '8px',
            }}>
              {overallSource === 'estimated' ? '~' : ''}{overallValue}
            </div>
            <div style={{ ...MONO, fontSize: '9px', color: '#444', letterSpacing: '1px' }}>
              {overallLabel}
            </div>
            <div style={{ marginTop: '10px', opacity: overallSource === 'estimated' ? 0.55 : 1 }}>
              <ProgressBar
                value={parseInt(overallValue.replace(/[^0-9]/g, '')) || 0}
                color={overallSource === 'estimated' ? EST_COLOR : barColor}
              />
            </div>
          </div>

          <div>
            <div style={{ ...MONO, fontSize: '9px', color: '#555', letterSpacing: '2px', marginBottom: '12px' }}>
              PER-FEATURE BREAKDOWN
            </div>
            {features.map((f) => (
              <FeatureRow key={f.label} label={f.label} value={f.value} source={f.source} />
            ))}
          </div>
        </div>

        {/* Right: real stats + note */}
        <div>
          <div style={{ marginBottom: '18px' }}>
            <div style={{ ...MONO, fontSize: '9px', color: '#555', letterSpacing: '2px', marginBottom: '10px' }}>
              MODULE STATS
            </div>
            <div style={{ borderLeft: `2px solid ${accentColor}44`, paddingLeft: '12px' }}>
              {statsRows.map((row) => (
                <div key={row.label} style={{ marginBottom: '8px' }}>
                  <div style={{ ...MONO, fontSize: '9px', color: '#555', letterSpacing: '1px' }}>
                    {row.label}
                    <SourceBadge source={row.source} />
                  </div>
                  <div style={{
                    ...MONO, fontSize: '16px', marginTop: '2px',
                    color: row.source === 'estimated' ? EST_COLOR : '#d0d0d0',
                  }}>
                    {row.source === 'estimated' ? '~' : ''}{row.value}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div style={{
            padding: '12px 14px',
            background: `${accentColor}0d`,
            border: `1px solid ${accentColor}22`,
            ...MONO, fontSize: '10px', color: '#888', letterSpacing: '0.3px', lineHeight: 1.6,
          }}>
            ℹ {note}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── SVG Trend Chart ────────────────────────────────────────────────────────

const CHART_W = 600;
const CHART_H = 200;
const CHART_PAD = { left: 44, right: 20, top: 15, bottom: 36 };
const CHART_CW = CHART_W - CHART_PAD.left - CHART_PAD.right;
const CHART_CH = CHART_H - CHART_PAD.top - CHART_PAD.bottom;

function last30Days(): string[] {
  const today = new Date();
  return Array.from({ length: 30 }, (_, i) => {
    const d = new Date(today);
    d.setDate(d.getDate() - 29 + i);
    return d.toISOString().split('T')[0];
  });
}

function xAt(i: number): number {
  return CHART_PAD.left + (i / 29) * CHART_CW;
}

function yAt(v: number): number {
  return CHART_PAD.top + CHART_CH - (v / 100) * CHART_CH;
}

function buildSvgPath(map: Map<string, number>, days: string[]): string {
  let d = '';
  let inLine = false;
  for (let i = 0; i < days.length; i++) {
    const v = map.get(days[i]);
    if (v == null) { inLine = false; continue; }
    const x = xAt(i).toFixed(1);
    const y = yAt(v).toFixed(1);
    d += inLine ? `L${x} ${y} ` : `M${x} ${y} `;
    inLine = true;
  }
  return d;
}

function fmtDay(iso: string): string {
  const d = new Date(iso);
  return `${d.getDate()} ${d.toLocaleString('en', { month: 'short' })}`;
}

function TrendChart({ trend }: { trend: TrendData }) {
  const days = last30Days();
  const kaavalMap = new Map(trend.kaaval_trend.map((p) => [p.day, p.avg_confidence]));
  const veriffMap = new Map(trend.veriff_trend.map((p) => [p.day, p.avg_confidence]));
  const overallMap = new Map(trend.overall_trend.map((p) => [p.day, p.confidence]));

  const totalPoints =
    trend.kaaval_trend.length + trend.veriff_trend.length + trend.overall_trend.length;

  if (totalPoints < 3) {
    return (
      <div style={{ padding: '40px', textAlign: 'center' }}>
        <div style={{ ...MONO, fontSize: '11px', color: '#555', letterSpacing: '1.5px' }}>
          INSUFFICIENT DATA — confidence trend builds over time
        </div>
        <div style={{ marginTop: '8px', ...MONO, fontSize: '10px', color: '#444' }}>
          {totalPoints} data point{totalPoints !== 1 ? 's' : ''} collected so far
        </div>
      </div>
    );
  }

  const yGrid = [0, 25, 50, 75, 100];
  const xLabels = [0, 5, 10, 15, 20, 25, 29];

  return (
    <svg
      viewBox={`0 0 ${CHART_W} ${CHART_H}`}
      style={{ width: '100%', height: 'auto', display: 'block' }}
      aria-label="Confidence trend — last 30 days"
    >
      {/* Horizontal grid lines + Y labels */}
      {yGrid.map((v) => (
        <g key={v}>
          <line
            x1={CHART_PAD.left} y1={yAt(v)}
            x2={CHART_W - CHART_PAD.right} y2={yAt(v)}
            stroke={v === 0 ? '#2a2a2a' : 'rgba(255,255,255,0.04)'}
            strokeWidth={1}
          />
          <text
            x={CHART_PAD.left - 6} y={yAt(v) + 3.5}
            textAnchor="end"
            style={{ fontFamily: "'Space Mono', monospace", fontSize: '9px', fill: '#444' }}
          >
            {v}%
          </text>
        </g>
      ))}

      {/* X axis baseline */}
      <line
        x1={CHART_PAD.left} y1={CHART_H - CHART_PAD.bottom}
        x2={CHART_W - CHART_PAD.right} y2={CHART_H - CHART_PAD.bottom}
        stroke="#2a2a2a" strokeWidth={1}
      />

      {/* Series lines */}
      <path d={buildSvgPath(kaavalMap, days)} stroke="#ff8c00" strokeWidth={1.5} fill="none" strokeLinejoin="round" />
      <path d={buildSvgPath(veriffMap, days)} stroke="#4ade80" strokeWidth={1.5} fill="none" strokeLinejoin="round" />
      <path d={buildSvgPath(overallMap, days)} stroke="#3a8eff" strokeWidth={1.5} fill="none" strokeLinejoin="round" strokeDasharray="4 2" />

      {/* Data point dots */}
      {days.map((day, i) => {
        const kv = kaavalMap.get(day);
        const vv = veriffMap.get(day);
        const ov = overallMap.get(day);
        return (
          <g key={day}>
            {kv != null && <circle cx={xAt(i)} cy={yAt(kv)} r={2.5} fill="#ff8c00" />}
            {vv != null && <circle cx={xAt(i)} cy={yAt(vv)} r={2.5} fill="#4ade80" />}
            {ov != null && <circle cx={xAt(i)} cy={yAt(ov)} r={2} fill="#3a8eff" opacity={0.8} />}
          </g>
        );
      })}

      {/* X axis date labels */}
      {xLabels.map((i) => (
        i < days.length ? (
          <text
            key={i} x={xAt(i)} y={CHART_H - CHART_PAD.bottom + 14}
            textAnchor="middle"
            style={{ fontFamily: "'Space Mono', monospace", fontSize: '8px', fill: '#444' }}
          >
            {fmtDay(days[i])}
          </text>
        ) : null
      ))}
    </svg>
  );
}

export default function AccuracyPage() {
  const [data, setData] = useState<AccuracyData | null>(null);
  const [trend, setTrend] = useState<TrendData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/accuracy/dashboard`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setData)
      .catch(() => setError('Failed to load accuracy data.'));

    fetch(`${API_URL}/api/accuracy/trend`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d) setTrend(d); })
      .catch(() => {});
  }, []);

  if (error) {
    return (
      <div className="page">
        <div style={{ ...MONO, fontSize: '12px', color: 'var(--critical)', padding: '40px' }}>{error}</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="page">
        <div style={{ ...MONO, fontSize: '11px', color: '#555', padding: '40px', letterSpacing: '1.5px' }}>
          LOADING ACCURACY DATA…
        </div>
      </div>
    );
  }

  const { kaaval, kavasam, veriff, overall } = data;

  const toFeatures = (
    features: Record<string, Metric>,
    label: (k: string) => string = (k) => FEATURE_LABELS[k] ?? k,
  ) => Object.entries(features).map(([k, m]) => ({
    label: label(k),
    value: m.value ?? 0,
    source: m.data_source,
  }));

  const kaavalFeatures = toFeatures(kaaval.features);
  const kavasamFeatures = toFeatures(kavasam.features);
  const veriffFeatures = toFeatures(veriff.features, (k) =>
    k === 'celebrity_entertainment' ? 'Celebrity / entertainment (capped)' : (FEATURE_LABELS[k] ?? k));

  const verdictBreakdown = veriff.verdict_breakdown.value ?? {};
  const veriffVerdictTotal = Object.values(verdictBreakdown).reduce((a, b) => a + b, 0);
  const num = (m: Metric) => (m.value ?? 0).toLocaleString();

  return (
    <div className="page" id="page-accuracy">
      {/* Page header */}
      <div className="page-header">
        <div className="page-label">PLATFORM · ACCURACY & RELIABILITY</div>
        <h1 className="page-title">
          நம்பகத்தன்மை
          <br />
          <span className="accent">Precision Metrics.</span>
        </h1>
        <p className="page-sub">
          Per-module accuracy breakdown combining real database statistics with
          honest estimated ranges. Estimates are labeled as such — precision
          improves with editorial feedback over time.
        </p>
      </div>

      {/* Overall reliability banner */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '1px',
        border: '1px solid var(--line)', marginBottom: '32px',
        background: 'var(--line)',
      }}>
        {[
          {
            label: 'Platform Reliability',
            value: `~${overall.platform_reliability.value}%`,
            color: EST_COLOR,
            source: overall.platform_reliability.data_source,
          },
          { label: 'Most Accurate', value: overall.most_accurate_module, color: 'var(--blue)', source: 'estimated' as DataSource },
          { label: 'Needs Work', value: overall.least_accurate_module, color: 'var(--medium)', source: 'estimated' as DataSource },
          { label: 'Channels Monitored', value: num(kavasam.channels_monitored), color: 'var(--safe)', source: kavasam.channels_monitored.data_source },
        ].map((item) => (
          <div key={item.label} style={{ background: 'var(--bg)', padding: '20px 18px' }}>
            <div style={{ ...MONO, fontSize: '9px', color: '#555', letterSpacing: '2px', marginBottom: '8px' }}>
              {item.label.toUpperCase()}
              <SourceBadge source={item.source} />
            </div>
            <div style={{ ...MONO, fontSize: '20px', color: item.color }}>{item.value}</div>
          </div>
        ))}
      </div>

      {/* Module cards */}
      <ModuleCard
        title="KAAVAL AI"
        subtitle="Threat intelligence · 5 agents"
        accentColor="#ff8c00"
        overallLabel={kaaval.precision_label}
        overallValue={`${kaaval.precision_low.value}–${kaaval.precision_high.value}%`}
        overallSource={kaaval.precision_low.data_source}
        barColor="#ff8c00"
        features={kaavalFeatures}
        statsRows={[
          { label: 'THREATS DETECTED', value: num(kaaval.total_threats), source: kaaval.total_threats.data_source },
          { label: 'CONFIRMED', value: num(kaaval.confirmed_threats), source: kaaval.confirmed_threats.data_source },
          { label: 'DISMISSED', value: num(kaaval.dismissed_threats), source: kaaval.dismissed_threats.data_source },
          {
            label: 'OBSERVED DISMISSAL RATE',
            value: kaaval.false_positive_rate.value != null
              ? `${kaaval.false_positive_rate.value}%`
              : 'Insufficient data',
            source: kaaval.false_positive_rate.data_source,
          },
        ]}
        note={kaaval.note}
      />

      <ModuleCard
        title="DIGITAL KAVASAM"
        subtitle={`Channel health monitoring · ${kavasam.channels_monitored.value ?? '—'} channels`}
        accentColor="#3a8eff"
        overallLabel="API-based detection — estimated, not measured"
        overallValue={`${kavasam.detection_rate.value}%`}
        overallSource={kavasam.detection_rate.data_source}
        barColor="var(--safe)"
        features={kavasamFeatures}
        statsRows={[
          { label: 'CHANNELS MONITORED', value: num(kavasam.channels_monitored), source: kavasam.channels_monitored.data_source },
          { label: 'ANOMALIES DETECTED', value: num(kavasam.total_anomalies), source: kavasam.total_anomalies.data_source },
          { label: 'CERT-IN DRAFTS', value: num(kavasam.cert_in_drafts), source: kavasam.cert_in_drafts.data_source },
          { label: 'FALSE POSITIVE RATE', value: kavasam.false_positive_rate_label.value ?? '—', source: kavasam.false_positive_rate_label.data_source },
        ]}
        note={kavasam.note}
      />

      <ModuleCard
        title="VERIFF TAMIL"
        subtitle="Fact-checking · Tamil claim extraction"
        accentColor="#4ade80"
        overallLabel={veriff.precision_label}
        overallValue={`${veriff.precision_low.value}–${veriff.precision_high.value}%`}
        overallSource={veriff.precision_low.data_source}
        barColor="var(--medium)"
        features={veriffFeatures}
        statsRows={[
          { label: 'TOTAL CHECKS', value: num(veriff.total_checks), source: veriff.total_checks.data_source },
          { label: 'UNIQUE VERDICTS', value: String(Object.keys(verdictBreakdown).length), source: veriff.verdict_breakdown.data_source },
          { label: 'TOTAL VERDICTS ISSUED', value: veriffVerdictTotal.toLocaleString(), source: veriff.verdict_breakdown.data_source },
          {
            label: 'AVG VERDICT TIME',
            value: veriff.avg_verdict_time_seconds.value != null
              ? `${Math.round(veriff.avg_verdict_time_seconds.value)}s`
              : '—',
            source: veriff.avg_verdict_time_seconds.data_source,
          },
        ]}
        note={veriff.note}
      />

      {/* Verdict breakdown */}
      {Object.keys(verdictBreakdown).length > 0 && (
        <div style={{
          border: '1px solid var(--line)', marginBottom: '32px', padding: '20px 22px',
        }}>
          <div style={{ ...MONO, fontSize: '9px', color: '#555', letterSpacing: '2px', marginBottom: '14px' }}>
            VERIFF VERDICT BREAKDOWN
            <SourceBadge source={veriff.verdict_breakdown.data_source} />
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px' }}>
            {Object.entries(verdictBreakdown).map(([verdict, count]) => (
              <div key={verdict} style={{
                padding: '8px 14px',
                border: '1px solid var(--line)',
                background: 'rgba(255,255,255,0.02)',
              }}>
                <div style={{ ...MONO, fontSize: '9px', color: '#666', letterSpacing: '1px' }}>{verdict}</div>
                <div style={{ ...MONO, fontSize: '18px', color: '#d0d0d0', marginTop: '2px' }}>{count}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* How accuracy improves */}
      <div style={{ border: '1px solid var(--line)', padding: '24px 22px', marginBottom: '32px' }}>
        <div style={{ ...MONO, fontSize: '9px', color: '#555', letterSpacing: '2px', marginBottom: '20px' }}>
          HOW ACCURACY IMPROVES
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '20px' }}>
          {[
            {
              step: '01',
              title: 'Editorial Feedback Loop',
              body: 'Editorial team confirms or dismisses findings → system learns signal from noise → precision increases.',
              color: 'var(--accent)',
            },
            {
              step: '02',
              title: 'Semantic Claim Matching',
              body: 'Veriff receives semantic claim matching → target 85% precision, up from an estimated 70–78%.',
              color: 'var(--blue)',
            },
            {
              step: '03',
              title: '30-Day Feedback Horizon',
              body: 'After 30 days of editorial signals, platform-wide reliability is projected to reach 90%+.',
              color: 'var(--safe)',
            },
          ].map((item) => (
            <div key={item.step} style={{ borderLeft: `2px solid ${item.color}`, paddingLeft: '16px' }}>
              <div style={{ ...MONO, fontSize: '9px', color: '#444', letterSpacing: '2px', marginBottom: '6px' }}>
                STEP {item.step}
              </div>
              <div style={{ ...MONO, fontSize: '12px', color: item.color, marginBottom: '8px', letterSpacing: '0.5px' }}>
                {item.title}
              </div>
              <div style={{ fontSize: '12px', color: '#888', lineHeight: 1.6 }}>
                {item.body}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Confidence trend chart */}
      <div style={{ border: '1px solid var(--line)', marginBottom: '32px' }}>
        <div style={{
          padding: '16px 22px 12px',
          borderBottom: '1px solid var(--line)',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px',
        }}>
          <div style={{ ...MONO, fontSize: '9px', color: '#555', letterSpacing: '2px' }}>
            CONFIDENCE TREND · LAST 30 DAYS
          </div>
          {/* Legend */}
          <div style={{ display: 'flex', gap: '18px' }}>
            {[
              { color: '#ff8c00', label: 'KAAVAL AI' },
              { color: '#4ade80', label: 'VERIFF TAMIL' },
              { color: '#3a8eff', label: 'OVERALL', dashed: true },
            ].map(({ color, label, dashed }) => (
              <div key={label} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <svg width={22} height={6} style={{ flexShrink: 0 }}>
                  <line
                    x1={0} y1={3} x2={22} y2={3}
                    stroke={color} strokeWidth={1.5}
                    strokeDasharray={dashed ? '4 2' : undefined}
                  />
                  <circle cx={11} cy={3} r={2.5} fill={color} />
                </svg>
                <span style={{ ...MONO, fontSize: '9px', color: '#555', letterSpacing: '1px' }}>{label}</span>
              </div>
            ))}
          </div>
        </div>
        <div style={{ padding: '12px 8px 4px', overflowX: 'auto' }}>
          {trend ? (
            <TrendChart trend={trend} />
          ) : (
            <div style={{ padding: '40px', textAlign: 'center', ...MONO, fontSize: '11px', color: '#555', letterSpacing: '1px' }}>
              LOADING TREND DATA…
            </div>
          )}
        </div>
      </div>

      {/* Disclaimer */}
      <div style={{
        padding: '14px 18px',
        background: 'rgba(255,91,58,0.05)', border: '1px solid rgba(255,91,58,0.2)',
        ...MONO, fontSize: '10px', color: '#666', letterSpacing: '0.3px', lineHeight: 1.6,
      }}>
        ⚠ <strong>MEASURED</strong> — {data.legend.measured}
        <br />
        ⚠ <strong>ESTIMATED</strong> — {data.legend.estimated} Estimated values are shown with a
        “~” prefix, in amber, and with a dimmed bar so they are never mistaken for measurements.
      </div>
    </div>
  );
}

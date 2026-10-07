'use client';

import { useEffect, useRef, useState } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

const EXT_TO_MEDIA: Record<string, string> = {
  mp4: 'video', avi: 'video', mov: 'video', webm: 'video', mkv: 'video',
  mp3: 'audio', wav: 'audio', ogg: 'audio', m4a: 'audio', flac: 'audio',
  jpg: 'image', jpeg: 'image', png: 'image', gif: 'image', webp: 'image', bmp: 'image',
};

function mediaTypeFromFilename(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() ?? '';
  return EXT_TO_MEDIA[ext] ?? 'text';
}

const VERDICT_COLOR: Record<string, string> = {
  REAL: 'var(--safe)',
  RECYCLED: 'var(--high)',
  MANIPULATED: 'var(--accent)',
  FAKE: 'var(--accent)',
  UNVERIFIED: '#666',
};

const RECO_COLOR: Record<string, string> = {
  CLEAR: 'var(--safe)',
  HOLD: 'var(--high)',
  INVESTIGATE: 'var(--accent)',
};

const RISK_COLOR: Record<string, string> = {
  LOW: 'var(--safe)',
  MEDIUM: 'var(--high)',
  HIGH: 'var(--accent)',
};

// Friendly display names for known API keys (7 real scrapers, no BOOM)
const SOURCE_LABELS: Record<string, string> = {
  pib: 'PIB', pib_fact_check: 'PIB',
  altnews: 'AltNews', alt_news: 'AltNews',
  vishvas: 'Vishvas News', vishvas_news: 'Vishvas News',
  fact_crescendo: 'Fact Crescendo Tamil', factcrescendo: 'Fact Crescendo Tamil',
  india_today: 'India Today', indiatoday: 'India Today',
  quint_webqoof: 'Quint WebQoof', quintwebooof: 'Quint WebQoof',
  newschecker_tamil: 'Newschecker Tamil', newschecker: 'Newschecker Tamil',
};

function friendlyName(key: string): string {
  return SOURCE_LABELS[key] ?? key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function entryMatch(value: unknown): boolean | null {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'object' && value !== null) {
    const obj = value as Record<string, unknown>;
    if ('matched' in obj) return Boolean(obj.matched);
    if ('match' in obj) return Boolean(obj.match);
    if ('found' in obj) return Boolean(obj.found);
    return Object.keys(obj).length > 0;
  }
  if (typeof value === 'string') return value.toLowerCase() !== 'no match' && value !== '';
  return null;
}

const CRED_TIER_COLOR: Record<string, string> = {
  HIGH: 'var(--safe)',
  MEDIUM: '#a0a030',
  LOW: 'var(--high)',
  SUSPICIOUS: 'var(--accent)',
  UNKNOWN: '#444',
};

const WEB_CRED_COLOR: Record<string, string> = {
  CREDIBLE: 'var(--safe)',
  MIXED: '#a0a030',
  'LOW CREDIBILITY': 'var(--high)',
  'NO WEB PRESENCE': '#444',
  UNKNOWN: '#444',
};

interface WebSearchItem {
  source: string;
  url: string;
  headline: string;
  verdict: string;
  date: string;
  credibility?: { domain: string; score: number; tier: string };
}

interface RelatedClaim {
  claim_text: string;
  verdict: string;
  confidence: number;
  checked_at: string | null;
  similarity: number;
}

interface ClaimEntities {
  who: string;
  what: string;
  when: string;
  where: string;
  keywords: string[];
}

interface VeriffResult {
  verdict?: string;
  confidence?: number;
  deepfake_score?: number;
  verdict_time_seconds?: number;
  database_results?: Record<string, unknown>;
  claim_text?: string;
  web_search_results?: WebSearchItem[];
  web_sources_checked?: number;
  entities?: ClaimEntities;
  related_claims?: RelatedClaim[];
  web_credibility_score?: number;
  web_credibility_label?: string;
  // judge fields may be top-level or nested
  judge_final_verdict?: string;
  judge_broadcast_recommendation?: string;
  judge_legal_risk?: string;
  judge_reasoning?: string;
  judge_confidence?: number;
  judge?: {
    judge_final_verdict?: string;
    judge_broadcast_recommendation?: string;
    judge_legal_risk?: string;
    judge_reasoning?: string;
    judge_confidence?: number;
  };
}

function pick<T>(a: T | undefined, b: T | undefined): T | undefined {
  return a ?? b;
}

export function VeriffVerifyPanel() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const verdictRef = useRef<HTMLDivElement>(null);

  const [droppedFile, setDroppedFile] = useState<File | null>(null);
  const [textClaim, setTextClaim] = useState('');
  const [language, setLanguage] = useState<'tamil' | 'english'>('tamil');
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [dots, setDots] = useState('');
  const [result, setResult] = useState<VeriffResult | null>(null);
  const [error, setError] = useState('');
  const [judgeOpen, setJudgeOpen] = useState(false);

  useEffect(() => {
    if (!loading) { setDots(''); return; }
    const id = setInterval(() => setDots((d) => (d.length >= 3 ? '' : d + '.')), 500);
    return () => clearInterval(id);
  }, [loading]);

  function onDragOver(e: React.DragEvent) {
    e.preventDefault();
    setDragging(true);
  }
  function onDragLeave() { setDragging(false); }
  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) { setDroppedFile(file); setTextClaim(''); }
  }
  function onZoneClick() { fileInputRef.current?.click(); }
  function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) { setDroppedFile(file); setTextClaim(''); }
  }

  async function handleSubmit() {
    const claimText = textClaim.trim() || droppedFile?.name || '';
    if (!claimText) { setError('Enter a text claim or drop a file.'); return; }

    const mediaType = textClaim.trim()
      ? 'text'
      : mediaTypeFromFilename(droppedFile?.name ?? '');

    setLoading(true);
    setError('');
    setResult(null);

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 180000);
    try {
      const form = new FormData();
      form.append('claim_text', claimText);
      form.append('media_type', mediaType);
      form.append('language', language);
      form.append('checked_by', 'journalist');
      // Attach the actual dropped file so the API can run the real deepfake
      // detector on it — previously only the filename was sent, so media
      // claims never got a real analysis.
      if (droppedFile && mediaType !== 'text') {
        form.append('file', droppedFile);
      }
      const res = await fetch(`${API_URL}/api/veriff/check`, {
        method: 'POST',
        signal: controller.signal,
        body: form,
      });
      clearTimeout(timeoutId);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `API error ${res.status}`);
      }
      const data: VeriffResult = await res.json();
      setResult(data);
      setTimeout(() => verdictRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 100);
    } catch (err) {
      clearTimeout(timeoutId);
      if (err instanceof Error && err.name === 'AbortError') {
        setError('Verification timed out — Judge LLM is processing. Please try again.');
      } else {
        setError(err instanceof Error ? err.message : 'Request failed');
      }
    } finally {
      setLoading(false);
    }
  }

  // normalise judge fields — they may be top-level or nested under .judge
  const j = result?.judge ?? {};
  const finalVerdict = pick(result?.judge_final_verdict, j.judge_final_verdict);
  const broadcastReco = pick(result?.judge_broadcast_recommendation, j.judge_broadcast_recommendation);
  const legalRisk = pick(result?.judge_legal_risk, j.judge_legal_risk);
  const judgeReasoning = pick(result?.judge_reasoning, j.judge_reasoning);
  const judgeConfidence = pick(result?.judge_confidence, j.judge_confidence);

  const verdictKey = result?.verdict ?? '';
  const verdictColor = VERDICT_COLOR[verdictKey] ?? '#888';
  const confidence = result?.confidence ?? 0;
  const gaugeWidth = `${Math.round(confidence * 100)}%`;

  return (
    <>
      {/* language toggle */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
        <span style={{ fontFamily: "'Space Mono', monospace", fontSize: '9px', color: '#555', letterSpacing: '2px', textTransform: 'uppercase' }}>
          Language
        </span>
        {(['tamil', 'english'] as const).map((lang) => (
          <button
            key={lang}
            onClick={() => setLanguage(lang)}
            style={{
              padding: '4px 14px',
              fontFamily: "'Space Mono', monospace",
              fontSize: '10px',
              letterSpacing: '1px',
              textTransform: 'uppercase',
              border: '1px solid',
              borderColor: language === lang ? 'var(--accent)' : '#222',
              background: language === lang ? 'rgba(255,91,58,0.1)' : 'transparent',
              color: language === lang ? 'var(--accent)' : '#555',
              cursor: 'pointer',
            }}
          >
            {lang}
          </button>
        ))}
      </div>

      {/* drop zone */}
      <div
        className="verify-zone"
        onClick={onZoneClick}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        style={dragging ? { borderColor: 'var(--accent)', background: 'rgba(255,91,58,0.05)' } : undefined}
      >
        <div className="icon">⤓</div>
        {droppedFile ? (
          <>
            <div className="label" style={{ color: 'var(--safe)' }}>{droppedFile.name}</div>
            <div className="hint">{mediaTypeFromFilename(droppedFile.name).toUpperCase()} · click to change</div>
          </>
        ) : (
          <>
            <div className="label">DROP MEDIA TO VERIFY</div>
            <div className="hint">Image · Video clip · Audio file · Tamil or English · Verdict in &lt;30 seconds</div>
          </>
        )}
        <input
          ref={fileInputRef}
          type="file"
          style={{ display: 'none' }}
          accept="image/*,video/*,audio/*"
          onChange={onFileChange}
        />
      </div>

      {/* text claim input */}
      <div style={{ margin: '16px 0' }}>
        <div style={{ fontFamily: "'Space Mono', monospace", fontSize: '9px', color: '#555', letterSpacing: '2px', textTransform: 'uppercase', marginBottom: '8px' }}>
          Or paste a text claim
        </div>
        <textarea
          value={textClaim}
          onChange={(e) => { setTextClaim(e.target.value); if (e.target.value) setDroppedFile(null); }}
          placeholder="சென்னையில் இன்று காலை நடைபெற்ற போராட்டத்தின் காட்சிகள் — paste Tamil or English claim here"
          rows={3}
          style={{
            width: '100%',
            background: '#050505',
            border: '1px solid #1f1f1f',
            color: '#d0d0d0',
            padding: '12px 14px',
            fontFamily: "'Space Mono', monospace",
            fontSize: '12px',
            lineHeight: 1.6,
            resize: 'vertical',
            outline: 'none',
            boxSizing: 'border-box',
          }}
        />
      </div>

      {/* submit */}
      <button
        onClick={handleSubmit}
        disabled={loading}
        style={{
          width: '100%',
          padding: '13px',
          background: loading ? '#222' : 'rgba(255,91,58,0.15)',
          border: '1px solid',
          borderColor: loading ? '#333' : 'rgba(255,91,58,0.4)',
          color: loading ? '#555' : 'var(--accent)',
          fontFamily: "'Bebas Neue', var(--font-bebas-neue), sans-serif",
          fontSize: '18px',
          letterSpacing: '3px',
          cursor: loading ? 'not-allowed' : 'pointer',
          marginBottom: '24px',
          transition: 'all 0.15s',
        }}
      >
        {loading ? `ANALYZING${dots}  Judge LLM evaluating` : 'VERIFY NOW'}
      </button>

      {/* error */}
      {error && (
        <div style={{
          padding: '10px 14px',
          border: '1px solid rgba(255,56,56,0.3)',
          color: '#ff3838',
          fontFamily: "'Space Mono', monospace",
          fontSize: '11px',
          marginBottom: '20px',
        }}>
          {error}
        </div>
      )}

      {/* verdict panel */}
      {result && (
        <div className="verdict-box" id="verdictBox" ref={verdictRef}>
          {/* header row */}
          <div className="verdict-row">
            <div>
              <div className="page-label" style={{ marginBottom: '8px' }}>
                LATEST VERDICT · {(result.claim_text ?? textClaim) || droppedFile?.name}
              </div>
              <div
                className="verdict-label-big"
                style={{ color: verdictColor, borderColor: verdictColor }}
              >
                {verdictKey || 'UNVERIFIED'}
              </div>
              {finalVerdict && finalVerdict !== verdictKey && (
                <div style={{ fontFamily: "'Space Mono', monospace", fontSize: '10px', color: '#666', marginTop: '6px', letterSpacing: '1px' }}>
                  JUDGE → <span style={{ color: VERDICT_COLOR[finalVerdict] ?? '#888' }}>{finalVerdict}</span>
                </div>
              )}
            </div>
            <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
              <div className="verdict-conf">
                CONFIDENCE <span className="num">{(confidence * 100).toFixed(0)}%</span>
              </div>
              {result.verdict_time_seconds != null && (
                <div className="verdict-conf" style={{ marginTop: '4px' }}>
                  VERDICT TIME <span className="num">{result.verdict_time_seconds}s</span>
                </div>
              )}
              {broadcastReco && (
                <div style={{
                  marginTop: '8px',
                  fontFamily: "'Space Mono', monospace",
                  fontSize: '10px',
                  letterSpacing: '1px',
                  color: RECO_COLOR[broadcastReco] ?? '#888',
                }}>
                  ► {broadcastReco}
                </div>
              )}
              {legalRisk && (
                <div style={{
                  marginTop: '4px',
                  fontFamily: "'Space Mono', monospace",
                  fontSize: '10px',
                  letterSpacing: '1px',
                  color: RISK_COLOR[legalRisk] ?? '#888',
                }}>
                  LEGAL RISK: {legalRisk}
                </div>
              )}
              {result.web_credibility_label && result.web_credibility_label !== 'NO WEB PRESENCE' && (
                <div style={{
                  marginTop: '8px',
                  fontFamily: "'Space Mono', monospace",
                  fontSize: '10px',
                  letterSpacing: '1px',
                  color: WEB_CRED_COLOR[result.web_credibility_label] ?? '#555',
                }}>
                  WEB CREDIBILITY: {result.web_credibility_score?.toFixed(0)}/100 · {result.web_credibility_label}
                </div>
              )}
            </div>
          </div>

          {/* confidence gauge */}
          <div className="gauge">
            <div className="gauge-fill" style={{ width: gaugeWidth, background: verdictColor }} />
          </div>

          {/* deepfake analysis */}
          {(() => {
            const ds = result.deepfake_score ?? 0;
            const label = ds > 0.7
              ? { text: '⚠ HIGH DEEPFAKE PROBABILITY', color: 'var(--accent)' }
              : ds > 0
              ? { text: '✓ LOW DEEPFAKE RISK', color: 'var(--safe)' }
              : { text: '— Not analyzed', color: '#666' };
            return (
              <div style={{ display: 'flex', alignItems: 'center', gap: '16px', marginTop: '12px' }}>
                <span style={{ fontFamily: "'Space Mono', monospace", fontSize: '9px', color: '#555', letterSpacing: '2px', textTransform: 'uppercase' }}>
                  Deepfake Analysis
                </span>
                <span style={{ fontFamily: "'Space Mono', monospace", fontSize: '11px', color: label.color, letterSpacing: '0.5px' }}>
                  {label.text}
                </span>
              </div>
            );
          })()}

          {/* collapsible judge section */}
          {judgeReasoning && (
            <div style={{ marginTop: '16px', borderTop: '1px solid var(--line)', paddingTop: '14px' }}>
              <button
                type="button"
                onClick={() => setJudgeOpen((v) => !v)}
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
                  {judgeOpen ? '▾' : '▸'}
                </span>
                Why did the system flag this?
              </button>

              {judgeOpen && (
                <div style={{ marginTop: '14px', display: 'grid', gap: '14px' }}>
                  <div style={{
                    fontFamily: "'Space Mono', monospace",
                    fontSize: '12px',
                    color: '#888',
                    lineHeight: 1.55,
                    background: 'var(--bg)',
                    borderLeft: '2px solid var(--accent)',
                    padding: '12px 16px',
                  }}>
                    {judgeReasoning}
                  </div>

                  <div style={{ display: 'flex', gap: '18px', flexWrap: 'wrap' }}>
                    {judgeConfidence != null && (
                      <div>
                        <div style={{ fontFamily: "'Space Mono', monospace", fontSize: '9px', color: '#555', letterSpacing: '2px', textTransform: 'uppercase', marginBottom: '4px' }}>
                          Judge confidence · {Math.round(judgeConfidence * 100)}%
                        </div>
                        <div style={{ width: '120px', height: '4px', background: '#1a1a1a', overflow: 'hidden' }}>
                          <div style={{
                            width: `${Math.round(judgeConfidence * 100)}%`,
                            height: '100%',
                            background: 'var(--accent)',
                            boxShadow: '0 0 8px var(--accent)',
                          }} />
                        </div>
                      </div>
                    )}

                    {broadcastReco && (
                      <div>
                        <div style={{ fontFamily: "'Space Mono', monospace", fontSize: '9px', color: '#555', letterSpacing: '2px', textTransform: 'uppercase', marginBottom: '4px' }}>
                          Broadcast recommendation
                        </div>
                        <div style={{
                          fontFamily: "'Space Mono', monospace",
                          fontSize: '12px',
                          color: RECO_COLOR[broadcastReco] ?? '#888',
                          letterSpacing: '1px',
                        }}>
                          {broadcastReco}
                        </div>
                      </div>
                    )}

                    {legalRisk && (
                      <div>
                        <div style={{ fontFamily: "'Space Mono', monospace", fontSize: '9px', color: '#555', letterSpacing: '2px', textTransform: 'uppercase', marginBottom: '4px' }}>
                          Legal risk
                        </div>
                        <div style={{
                          fontFamily: "'Space Mono', monospace",
                          fontSize: '12px',
                          color: RISK_COLOR[legalRisk] ?? '#888',
                          letterSpacing: '1px',
                        }}>
                          {legalRisk}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* claim entity analysis */}
          {result.entities && (result.entities.who || result.entities.what || result.entities.when || result.entities.where) && (
            <div style={{ marginTop: '16px', borderTop: '1px solid var(--line)', paddingTop: '12px' }}>
              <div className="page-label" style={{ marginBottom: '8px' }}>CLAIM ANALYSIS</div>
              <div style={{ fontFamily: "'Space Mono', monospace", fontSize: '10px', color: '#555', lineHeight: 1.8, letterSpacing: '0.5px' }}>
                {result.entities.who && <span><span style={{ color: '#333' }}>WHO</span> {result.entities.who} · </span>}
                {result.entities.what && <span><span style={{ color: '#333' }}>WHAT</span> {result.entities.what} · </span>}
                {result.entities.when && <span><span style={{ color: '#333' }}>WHEN</span> {result.entities.when} · </span>}
                {result.entities.where && <span><span style={{ color: '#333' }}>WHERE</span> {result.entities.where}</span>}
              </div>
            </div>
          )}

          {/* 7-database results — rendered dynamically from whatever the API returns */}
          {result.database_results && Object.keys(result.database_results).length > 0 && (
            <>
              <div className="page-label" style={{ margin: '18px 0 10px' }}>
                7-DATABASE CROSS-CHECK
              </div>
              <div className="verdict-sources">
                {Object.entries(result.database_results).map(([key, value]) => {
                  const matched = entryMatch(value);
                  return (
                    <div className="verdict-source" key={key}>
                      <div className="name">{friendlyName(key)}</div>
                      <div className={`status ${matched === null ? '' : matched ? 'match' : 'nomatch'}`}>
                        {matched === null ? '—' : matched ? 'Match' : 'No match'}
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}

          {/* web search results — informational only, with credibility badges */}
          {result.web_search_results && result.web_search_results.length > 0 && (
            <>
              <div className="page-label" style={{ margin: '18px 0 8px', opacity: 0.65 }}>
                WEB SEARCH RESULTS
                {result.web_sources_checked != null && (
                  <span style={{ fontWeight: 'normal', marginLeft: '8px', fontSize: '9px', opacity: 0.7 }}>
                    · {result.web_sources_checked} sources
                  </span>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
                {result.web_search_results.map((item, i) => {
                  const tier = item.credibility?.tier ?? 'UNKNOWN';
                  const tierColor = CRED_TIER_COLOR[tier] ?? '#444';
                  return (
                    <div key={i} style={{ fontFamily: "'Space Mono', monospace", fontSize: '10px', color: '#4a4a4a', lineHeight: 1.6, display: 'flex', alignItems: 'baseline', gap: '6px', flexWrap: 'wrap' }}>
                      <span style={{ color: '#333' }}>[{item.source}]</span>
                      {tier !== 'UNKNOWN' && (
                        <span style={{ color: tierColor, fontSize: '8px', letterSpacing: '1px', border: `1px solid ${tierColor}`, padding: '0 4px', opacity: 0.8 }}>
                          {tier}
                        </span>
                      )}
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{ color: '#555', textDecoration: 'none' }}
                      >
                        {item.headline}
                      </a>
                    </div>
                  );
                })}
              </div>
            </>
          )}

          {/* related past claims via pgvector similarity */}
          {result.related_claims && result.related_claims.length > 0 && (
            <>
              <div className="page-label" style={{ margin: '18px 0 8px', opacity: 0.65 }}>
                SIMILAR PAST VERDICTS
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {result.related_claims.map((rc, i) => {
                  const rColor = VERDICT_COLOR[rc.verdict] ?? '#666';
                  const date = rc.checked_at ? new Date(rc.checked_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '';
                  return (
                    <div key={i} style={{ fontFamily: "'Space Mono', monospace", fontSize: '10px', color: '#4a4a4a', lineHeight: 1.6, borderLeft: `2px solid ${rColor}`, paddingLeft: '10px' }}>
                      <span style={{ color: rColor, fontWeight: 'bold', marginRight: '6px' }}>{rc.verdict}</span>
                      <span style={{ color: '#333', marginRight: '6px' }}>· similarity {(rc.similarity * 100).toFixed(0)}%</span>
                      {date && <span style={{ color: '#2a2a2a', marginRight: '6px' }}>· {date}</span>}
                      <span style={{ color: '#3a3a3a', display: 'block', marginTop: '2px' }}>{rc.claim_text}</span>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      )}

      {/* empty state */}
      {!result && !loading && (
        <div style={{
          padding: '40px 0',
          textAlign: 'center',
          fontFamily: "'Space Mono', monospace",
          fontSize: '11px',
          color: '#3a3a3a',
          letterSpacing: '1px',
        }}>
          Drop media or paste claim above to begin verification
        </div>
      )}
    </>
  );
}

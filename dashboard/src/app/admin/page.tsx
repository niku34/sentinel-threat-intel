'use client';

import { useEffect, useState } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

interface KaavalCfg {
  deepfake_sensitivity: string;
  alert_frequency: string;
  keywords_tamil: string;
  keywords_english: string;
}

interface KavasamCfg {
  anomaly_threshold: string;
  scan_interval: string;
  certin_contact_email: string;
  bulk_deletion_threshold: string;
}

interface VeriffCfg {
  verdict_mode: string;
  confidence_threshold: string;
}

const KAAVAL_DEFAULTS: KaavalCfg = {
  deepfake_sensitivity: '78',
  alert_frequency: 'Real-time',
  keywords_tamil: 'ஜெயா, ஜெயா பிளஸ், அறிக்கை',
  keywords_english: 'Example Broadcaster, Example News, anchor name',
};

const KAVASAM_DEFAULTS: KavasamCfg = {
  anomaly_threshold: 'Medium · 3 standard deviations',
  scan_interval: 'Every 5 minutes',
  certin_contact_email: 'contact@example.com',
  bulk_deletion_threshold: '10 videos in 5 minutes',
};

const VERIFF_DEFAULTS: VeriffCfg = {
  verdict_mode: 'Auto + Human review',
  confidence_threshold: '0.85',
};

function SavedBadge({ visible }: { visible: boolean }) {
  if (!visible) return null;
  return (
    <span style={{
      fontFamily: "'Space Mono', monospace",
      fontSize: '11px',
      color: 'var(--safe)',
      letterSpacing: '1px',
      marginLeft: '12px',
    }}>
      ✓ Saved
    </span>
  );
}

// ── Anchor Voice Profiles ────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: "'Space Mono', monospace" };
const VOICE_CHANNELS = ['Example Broadcaster', 'Example News', 'Example Entertainment', 'Example Movies'];
const VOICE_ACCEPT = '.mp3,.wav,.mp4,.mov,.mkv,.m4a,audio/*,video/*';

interface VoiceProfile {
  id: string;
  anchor_name: string;
  channel: string;
  sample_duration_seconds: number | null;
  file_type: string | null;
  audio_source: string | null;
  created_at: string | null;
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).slice(0, 2);
  return parts.map((p) => p[0]?.toUpperCase() ?? '').join('') || '?';
}

function fmtDuration(sec: number | null): string {
  if (!sec && sec !== 0) return '—';
  const s = Math.round(sec);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m > 0 ? `${m}m ${r}s` : `${r}s`;
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  return new Intl.DateTimeFormat('en-IN', {
    day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(iso));
}

function EnrollModal({
  onClose,
  onEnrolled,
  onToast,
}: {
  onClose: () => void;
  onEnrolled: () => void;
  onToast: (msg: string, type: 'success' | 'error') => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [anchorName, setAnchorName] = useState('');
  const [channel, setChannel] = useState('Example News');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape' && !busy) onClose(); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose, busy]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    if (!file) { setErr('Please choose an audio or video file.'); return; }
    if (!anchorName.trim()) { setErr('Anchor name is required.'); return; }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('anchor_name', anchorName.trim());
      fd.append('channel', channel);
      const res = await fetch(`${API_URL}/api/voice/enroll`, { method: 'POST', body: fd });
      if (res.ok) {
        onToast('Anchor enrolled successfully', 'success');
        onEnrolled();
        onClose();
      } else {
        const d = await res.json().catch(() => ({}));
        setErr(d.detail ?? 'Enrollment failed.');
      }
    } catch {
      setErr('Network error. Please try again.');
    } finally {
      setBusy(false);
    }
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', boxSizing: 'border-box',
    background: 'var(--bg)', border: '1px solid var(--line)',
    color: 'var(--text)', ...MONO, fontSize: '12px',
    padding: '10px 12px', outline: 'none',
  };
  const labelStyle: React.CSSProperties = {
    ...MONO, fontSize: '9px', color: '#555',
    letterSpacing: '2px', textTransform: 'uppercase',
    display: 'block', marginBottom: '6px',
  };

  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}
      style={{
        position: 'fixed', inset: 0, zIndex: 150,
        background: 'rgba(0,0,0,0.85)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px',
      }}
    >
      <div style={{
        background: '#0a0a0a', border: '1px solid #1f1f1f',
        width: '100%', maxWidth: '460px', padding: '40px', position: 'relative',
      }}>
        <button
          onClick={onClose}
          disabled={busy}
          style={{
            position: 'absolute', top: '16px', right: '16px',
            background: 'none', border: 'none', color: '#555',
            fontSize: '18px', cursor: 'pointer', lineHeight: 1, padding: '4px 8px',
          }}
        >
          ✕
        </button>

        <div style={{ marginBottom: '24px' }}>
          <div style={{ ...MONO, fontSize: '9px', color: '#444', letterSpacing: '3px', textTransform: 'uppercase', marginBottom: '8px' }}>
            VOICE FINGERPRINT
          </div>
          <div style={{ fontSize: '18px', fontWeight: 600, color: 'var(--text)' }}>
            Enroll Anchor
          </div>
          <div style={{ width: '32px', height: '1px', background: 'var(--accent)', marginTop: '10px' }} />
        </div>

        <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
          <div>
            <label style={labelStyle}>Audio or Video File</label>
            <label
              style={{
                display: 'block', border: '1px dashed var(--line)', padding: '18px',
                textAlign: 'center', cursor: 'pointer', ...MONO, fontSize: '11px',
                color: file ? 'var(--text)' : 'var(--dim)',
              }}
            >
              {file
                ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB`
                : 'Drop audio or video file here — mp3, wav, mp4, mov, mkv, m4a'}
              <input
                type="file"
                accept={VOICE_ACCEPT}
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                style={{ display: 'none' }}
              />
            </label>
          </div>
          <div>
            <label style={labelStyle}>Anchor Name</label>
            <input
              type="text"
              value={anchorName}
              onChange={(e) => setAnchorName(e.target.value)}
              placeholder="e.g. Example News Prime Anchor"
              style={inputStyle}
              autoFocus
            />
          </div>
          <div>
            <label style={labelStyle}>Channel</label>
            <select
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
              style={inputStyle}
            >
              {VOICE_CHANNELS.map((c) => <option key={c}>{c}</option>)}
            </select>
          </div>

          {err && (
            <div style={{ ...MONO, fontSize: '10px', color: 'var(--critical)', letterSpacing: '0.5px' }}>
              ✕ {err}
            </div>
          )}

          <button
            type="submit"
            className="btn"
            disabled={busy}
            style={{ opacity: busy ? 0.6 : 1 }}
          >
            {busy ? 'Enrolling… (may take 5–10s)' : 'Enroll Anchor'}
          </button>
        </form>
      </div>
    </div>
  );
}

// ── Meta Integration ─────────────────────────────────────────────────────────

const BRAND_FIELDS: [string, string][] = [
  ['brand_main', 'Example Broadcaster'],
  ['brand_news', 'Example News'],
  ['brand_entertainment', 'Example Entertainment'],
  ['brand_movies', 'Example Movies'],
];

// Meta (Facebook Graph API) page slots — distinct from BRAND_FIELDS above,
// which tracks YouTube channels for brands Sentinel doesn't hold Meta page
// tokens for.
const META_BRAND_FIELDS: [string, string][] = [
  ['brand_main', 'Example Broadcaster'],
  ['brand_spiritual', 'Example Broadcaster Spiritual'],
  ['brand_entertainment', 'Example Entertainment'],
];

interface MetaStatus {
  configured: boolean;            // credential fields filled in — NOT verified
  connection_verified: boolean | null;
  enabled: boolean;
  missing_credentials: string[];
  page_tokens_set: number;
  distinct_page_tokens: number;
  ig_accounts_set: number;
  warnings: string[];
  meta_threat_score: number;
  last_scan: string | null;
}

interface MetaPageResult {
  name: string | null;
  page_id: string;
  fan_count: number | null;
  status: 'OK' | 'FAILED';
  error: string | null;
}
interface MetaTestResult { connected: boolean; pages: MetaPageResult[]; }
interface MetaScanResult {
  status?: string;
  fake_pages_found?: number;
  brigades_detected?: number;
  ig_misuse_found?: number;
  threats_created?: number;
  meta_threat_score?: number;
}

type BrandMap = Record<string, string>;
const EMPTY_BRAND_MAP: BrandMap = { brand_main: '', brand_news: '', brand_entertainment: '', brand_movies: '' };
const EMPTY_META_BRAND_MAP: BrandMap = { brand_main: '', brand_spiritual: '', brand_entertainment: '' };

function metaScoreColor(score: number): string {
  if (score <= 30) return 'var(--safe)';
  if (score <= 60) return 'var(--medium)';
  return 'var(--critical)';
}

// ── YouTube Monitoring (Digital Kavasam) ─────────────────────────────────────

interface KavasamStatus { api_configured: boolean; data_source: string; }

function YoutubeMonitoringSection() {
  const [status, setStatus] = useState<KavasamStatus | null>(null);
  const [apiKey, setApiKey] = useState('');
  const [channelIds, setChannelIds] = useState<BrandMap>({ ...EMPTY_BRAND_MAP });
  const [reveal, setReveal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [scanResult, setScanResult] = useState<string | null>(null);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);

  function showToast(msg: string, type: 'success' | 'error') {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4000);
  }

  function loadStatus() {
    fetch(`${API_URL}/api/kavasam/metrics`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d) setStatus({ api_configured: d.api_configured, data_source: d.data_source }); })
      .catch(() => {});
  }

  useEffect(() => { loadStatus(); }, []);

  async function save() {
    setSaving(true);
    try {
      const res = await fetch(`${API_URL}/api/kavasam/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ youtube_api_key: apiKey, channel_ids: channelIds }),
      });
      if (res.ok) { showToast('YouTube config saved', 'success'); loadStatus(); }
      else showToast('Save failed', 'error');
    } catch { showToast('Network error', 'error'); }
    finally { setSaving(false); }
  }

  async function scan() {
    setScanning(true);
    setScanResult(null);
    try {
      const res = await fetch(`${API_URL}/api/kavasam/scan`, { method: 'POST' });
      const d = await res.json();
      const anomalies = (d.anomalies ?? []).length;
      setScanResult(`${(d.channels ?? []).length} channels scanned · ${anomalies} anomalies · ${d.overall_status ?? '—'}`);
      showToast('Channel scan complete', 'success');
    } catch { showToast('Scan failed', 'error'); }
    finally { setScanning(false); }
  }

  const configured = status?.api_configured ?? false;
  const inputStyle: React.CSSProperties = {
    width: '100%', boxSizing: 'border-box', background: 'var(--bg)',
    border: '1px solid var(--line)', color: 'var(--text)', ...MONO,
    fontSize: '12px', padding: '10px 12px', outline: 'none',
  };

  return (
    <div className="admin-section">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '16px', flexWrap: 'wrap' }}>
        <div>
          <h3>YouTube Monitoring</h3>
          <div className="sub">
            Digital Kavasam channel health. YouTube Data API v3 when a key is set,
            otherwise public-page scraping. Both feed the same anomaly detector.
          </div>
        </div>
        <span style={{
          ...MONO, fontSize: '10px', letterSpacing: '1px', padding: '5px 12px', whiteSpace: 'nowrap',
          border: `1px solid ${configured ? 'rgba(74,222,128,0.4)' : 'rgba(255,159,67,0.4)'}`,
          background: configured ? 'rgba(74,222,128,0.12)' : 'rgba(255,159,67,0.12)',
          color: configured ? 'var(--safe)' : 'var(--high)',
        }}>
          {configured ? '● API CONFIGURED' : '○ USING PUBLIC SCRAPE'}
        </span>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '16px' }}>
        <button
          onClick={() => setReveal((v) => !v)}
          style={{ background: 'transparent', border: '1px solid var(--line)', color: 'var(--dim)', ...MONO, fontSize: '10px', letterSpacing: '1px', padding: '4px 10px', cursor: 'pointer' }}
        >
          {reveal ? 'HIDE' : 'SHOW'} KEY
        </button>
      </div>

      <div className="form-row">
        <div className="form-field">
          <label>YouTube Data API v3 Key</label>
          <input type={reveal ? 'text' : 'password'} value={apiKey} onChange={(e) => setApiKey(e.target.value)} style={inputStyle} autoComplete="off" placeholder="Leave blank to use public scraping" />
        </div>
      </div>
      <div className="form-row">
        {BRAND_FIELDS.slice(0, 2).map(([slug, label]) => (
          <div className="form-field" key={slug}>
            <label>Channel ID — {label}</label>
            <input type="text" value={channelIds[slug]} onChange={(e) => setChannelIds((p) => ({ ...p, [slug]: e.target.value }))} style={inputStyle} placeholder="UC…" />
          </div>
        ))}
      </div>
      <div className="form-row">
        {BRAND_FIELDS.slice(2).map(([slug, label]) => (
          <div className="form-field" key={slug}>
            <label>Channel ID — {label}</label>
            <input type="text" value={channelIds[slug]} onChange={(e) => setChannelIds((p) => ({ ...p, [slug]: e.target.value }))} style={inputStyle} placeholder="UC…" />
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', gap: '12px', marginTop: '14px', flexWrap: 'wrap', alignItems: 'center' }}>
        <button className="btn" onClick={save} disabled={saving} style={{ opacity: saving ? 0.6 : 1 }}>
          {saving ? 'Saving…' : 'SAVE'}
        </button>
        <button
          onClick={scan}
          disabled={scanning}
          style={{ background: 'transparent', border: '1px solid rgba(255,91,58,0.4)', color: 'var(--accent)', ...MONO, fontSize: '11px', letterSpacing: '1px', padding: '9px 16px', cursor: 'pointer' }}
        >
          {scanning ? 'Scanning…' : 'RUN CHANNEL SCAN'}
        </button>
        {scanResult && <span style={{ ...MONO, fontSize: '11px', color: 'var(--text-mid)' }}>{scanResult}</span>}
      </div>

      {toast && (
        <div style={{
          position: 'fixed', bottom: '24px', right: '24px', zIndex: 200, padding: '12px 20px',
          background: toast.type === 'success' ? 'rgba(74,222,128,0.12)' : 'rgba(255,56,56,0.12)',
          border: `1px solid ${toast.type === 'success' ? 'rgba(74,222,128,0.4)' : 'rgba(255,56,56,0.4)'}`,
          color: toast.type === 'success' ? 'var(--safe)' : 'var(--critical)',
          ...MONO, fontSize: '11px', letterSpacing: '0.5px',
        }}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}

function MetaIntegrationSection() {
  const [status, setStatus] = useState<MetaStatus | null>(null);
  const [appId, setAppId] = useState('');
  const [appSecret, setAppSecret] = useState('');
  const [pageTokens, setPageTokens] = useState<BrandMap>({ ...EMPTY_META_BRAND_MAP });
  const [pageIds, setPageIds] = useState<BrandMap>({ ...EMPTY_META_BRAND_MAP });
  const [igAccounts, setIgAccounts] = useState<BrandMap>({ ...EMPTY_META_BRAND_MAP });
  const [reveal, setReveal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<MetaTestResult | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanResult, setScanResult] = useState<MetaScanResult | null>(null);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);

  function showToast(msg: string, type: 'success' | 'error') {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4000);
  }

  function loadStatus() {
    fetch(`${API_URL}/api/meta/status`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d) setStatus(d); })
      .catch(() => {});
  }

  useEffect(() => { loadStatus(); }, []);

  async function save() {
    setSaving(true);
    try {
      const res = await fetch(`${API_URL}/api/meta/credentials`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          app_id: appId,
          app_secret: appSecret,
          page_tokens: pageTokens,
          page_ids: pageIds,
          ig_accounts: igAccounts,
        }),
      });
      if (res.ok) {
        showToast('Meta integration activated', 'success');
        loadStatus();
      } else {
        showToast('Save failed', 'error');
      }
    } catch {
      showToast('Network error', 'error');
    } finally {
      setSaving(false);
    }
  }

  async function test() {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await fetch(`${API_URL}/api/meta/test`);
      const d = await res.json();
      setTestResult(d);
    } catch {
      setTestResult({ connected: false, pages: [] });
    } finally {
      setTesting(false);
    }
  }

  async function scan() {
    setScanning(true);
    setScanResult(null);
    try {
      const res = await fetch(`${API_URL}/api/meta/scan`, { method: 'POST' });
      const d = await res.json();
      setScanResult(d);
      if (d.status === 'not_configured') {
        showToast('Not configured — enter credentials first', 'error');
      } else {
        showToast(`Scan complete — ${d.threats_created ?? 0} findings detected`, 'success');
        loadStatus();
      }
    } catch {
      showToast('Scan failed', 'error');
    } finally {
      setScanning(false);
    }
  }

  const configured = status?.configured ?? false;
  const score = status?.meta_threat_score ?? 0;

  const inputStyle: React.CSSProperties = {
    width: '100%', boxSizing: 'border-box',
    background: 'var(--bg)', border: '1px solid var(--line)',
    color: 'var(--text)', ...MONO, fontSize: '12px',
    padding: '10px 12px', outline: 'none',
  };

  function credField(label: string, value: string, onChange: (v: string) => void) {
    return (
      <div className="form-field" key={label}>
        <label>{label}</label>
        <input
          type={reveal ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          style={inputStyle}
          autoComplete="off"
        />
      </div>
    );
  }

  return (
    <div className="admin-section">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '16px', flexWrap: 'wrap' }}>
        <div>
          <h3>Meta Integration</h3>
          <div className="sub">
            Facebook + Instagram monitoring via the Meta Graph API. Detection only —
            findings route to editorial for review. &ldquo;Credentials set&rdquo; means the
            fields are filled in, not that they work — use Test Connection to verify.
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
          {/* status badge */}
          <span style={{
            ...MONO, fontSize: '10px', letterSpacing: '1px', padding: '5px 12px',
            border: `1px solid ${configured ? 'rgba(74,222,128,0.4)' : 'rgba(255,159,67,0.4)'}`,
            background: configured ? 'rgba(74,222,128,0.12)' : 'rgba(255,159,67,0.12)',
            color: configured ? 'var(--safe)' : 'var(--high)',
          }}>
            {configured ? '● CREDENTIALS SET' : '○ NOT CONFIGURED'}
          </span>
          {/* threat score widget */}
          <div style={{ textAlign: 'center' }}>
            <div style={{ ...MONO, fontSize: '8px', color: '#555', letterSpacing: '2px' }}>THREAT SCORE</div>
            <div style={{ fontSize: '28px', fontWeight: 700, color: configured ? metaScoreColor(score) : 'var(--dim)', lineHeight: 1.1 }}>
              {configured ? score : '—'}
            </div>
          </div>
        </div>
      </div>

      {(status?.warnings?.length ?? 0) > 0 && (
        <div style={{
          marginTop: '14px', padding: '12px 14px',
          background: 'rgba(255,176,32,0.06)', border: '1px solid rgba(255,176,32,0.25)',
          ...MONO, fontSize: '10px', color: '#b98a2e', lineHeight: 1.7,
        }}>
          {status!.warnings.map((w) => <div key={w}>⚠ {w}</div>)}
        </div>
      )}

      {!configured && (
        <div style={{
          marginTop: '16px', padding: '12px 16px',
          background: 'rgba(255,159,67,0.08)', border: '1px solid rgba(255,159,67,0.3)',
          ...MONO, fontSize: '11px', color: 'var(--high)', lineHeight: 1.5,
        }}>
          Enter credentials below. All stored encrypted on-premises.
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '16px' }}>
        <button
          onClick={() => setReveal((v) => !v)}
          style={{
            background: 'transparent', border: '1px solid var(--line)', color: 'var(--dim)',
            ...MONO, fontSize: '10px', letterSpacing: '1px', padding: '4px 10px', cursor: 'pointer',
          }}
        >
          {reveal ? 'HIDE' : 'SHOW'} CREDENTIALS
        </button>
      </div>

      <div className="form-row">
        {credField('App ID', appId, setAppId)}
        {credField('App Secret', appSecret, setAppSecret)}
      </div>
      <div className="form-row">
        {META_BRAND_FIELDS.map(([slug, label]) =>
          credField(`Page Token — ${label}`, pageTokens[slug], (v) =>
            setPageTokens((p) => ({ ...p, [slug]: v }))),
        )}
      </div>
      <div className="form-row">
        {META_BRAND_FIELDS.map(([slug, label]) =>
          credField(`Page ID — ${label}`, pageIds[slug], (v) =>
            setPageIds((p) => ({ ...p, [slug]: v }))),
        )}
      </div>
      <div className="form-row">
        {META_BRAND_FIELDS.map(([slug, label]) =>
          credField(`Instagram Account — ${label}`, igAccounts[slug], (v) =>
            setIgAccounts((p) => ({ ...p, [slug]: v }))),
        )}
      </div>

      <div style={{ display: 'flex', gap: '12px', marginTop: '14px', flexWrap: 'wrap', alignItems: 'center' }}>
        <button className="btn" onClick={save} disabled={saving} style={{ opacity: saving ? 0.6 : 1 }}>
          {saving ? 'Saving…' : 'SAVE & ACTIVATE'}
        </button>
        <button
          onClick={test}
          disabled={testing}
          style={{
            background: 'transparent', border: '1px solid var(--line)', color: 'var(--text)',
            ...MONO, fontSize: '11px', letterSpacing: '1px', padding: '9px 16px', cursor: 'pointer',
          }}
        >
          {testing ? 'Testing…' : 'TEST CONNECTION'}
        </button>
        <button
          onClick={scan}
          disabled={scanning}
          style={{
            background: 'transparent', border: '1px solid rgba(255,91,58,0.4)', color: 'var(--accent)',
            ...MONO, fontSize: '11px', letterSpacing: '1px', padding: '9px 16px', cursor: 'pointer',
          }}
        >
          {scanning ? 'Scanning…' : 'RUN META SCAN'}
        </button>
      </div>

      {testResult && (
        <div style={{ marginTop: '12px', ...MONO, fontSize: '11px' }}>
          <div style={{ color: testResult.connected ? 'var(--safe)' : 'var(--critical)', marginBottom: '6px' }}>
            {testResult.connected ? '✓ Connected — at least one page verified' : '✕ No page verified'}
          </div>
          {testResult.pages.length === 0 ? (
            <div style={{ color: 'var(--dim)' }}>No page tokens with a matching Page ID are saved.</div>
          ) : (
            testResult.pages.map((p) => (
              <div key={p.page_id} style={{ color: p.status === 'OK' ? 'var(--safe)' : 'var(--critical)' }}>
                {p.status === 'OK'
                  ? `✓ ${p.name} (${p.page_id}) — ${p.fan_count ?? '—'} followers`
                  : `✕ ${p.page_id} — ${p.error ?? 'failed'}`}
              </div>
            ))
          )}
        </div>
      )}

      {scanResult && scanResult.status !== 'not_configured' && (
        <div style={{ marginTop: '10px', ...MONO, fontSize: '11px', color: 'var(--text-mid)' }}>
          Detected: {scanResult.fake_pages_found ?? 0} fake pages ·{' '}
          {scanResult.brigades_detected ?? 0} brigades ·{' '}
          {scanResult.ig_misuse_found ?? 0} IG misuse →{' '}
          {scanResult.threats_created ?? 0} threats (score {scanResult.meta_threat_score ?? 0})
        </div>
      )}

      <div style={{
        marginTop: '16px', padding: '12px 16px',
        background: 'rgba(255,91,58,0.08)', border: '1px solid rgba(255,91,58,0.3)',
        ...MONO, fontSize: '10px', color: 'var(--accent)', letterSpacing: '0.5px', lineHeight: 1.5,
      }}>
        ⚠ Sentinel never auto-files or auto-strikes. All actions require editorial approval.
      </div>

      {toast && (
        <div style={{
          position: 'fixed', bottom: '24px', right: '24px', zIndex: 200, padding: '12px 20px',
          background: toast.type === 'success' ? 'rgba(74,222,128,0.12)' : 'rgba(255,56,56,0.12)',
          border: `1px solid ${toast.type === 'success' ? 'rgba(74,222,128,0.4)' : 'rgba(255,56,56,0.4)'}`,
          color: toast.type === 'success' ? 'var(--safe)' : 'var(--critical)',
          ...MONO, fontSize: '11px', letterSpacing: '0.5px',
        }}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}

// ── Rights Manager & Strike Tracker ──────────────────────────────────────────

interface RefFile {
  id: string;
  title: string;
  channel: string;
  broadcast_date: string | null;
  submitted_to_meta: boolean;
  status: string;
  created_at: string | null;
}

interface Strike {
  id: string;
  platform: string;
  content_url: string;
  status: string;
  filed_at: string | null;
  created_at: string | null;
}

const STRIKE_STATUS_COLOR: Record<string, string> = {
  DRAFT: 'var(--medium)',
  FILED: 'var(--blue)',
  CONFIRMED: 'var(--safe)',
  REJECTED: 'var(--critical)',
};

const modalInput: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box',
  background: 'var(--bg)', border: '1px solid var(--line)',
  color: 'var(--text)', ...MONO, fontSize: '12px', padding: '10px 12px', outline: 'none',
};
const modalLabel: React.CSSProperties = {
  ...MONO, fontSize: '9px', color: '#555', letterSpacing: '2px',
  textTransform: 'uppercase', display: 'block', marginBottom: '6px',
};

function UploadReferenceModal({
  onClose, onDone, onToast,
}: { onClose: () => void; onDone: () => void; onToast: (m: string, t: 'success' | 'error') => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [channel, setChannel] = useState('Example News');
  const [broadcastDate, setBroadcastDate] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape' && !busy) onClose(); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose, busy]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    if (!file) { setErr('Choose a video or audio file.'); return; }
    if (!title.trim()) { setErr('Title is required.'); return; }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('title', title.trim());
      fd.append('channel', channel);
      if (broadcastDate) fd.append('broadcast_date', broadcastDate);
      const res = await fetch(`${API_URL}/api/rights-manager/upload-reference`, { method: 'POST', body: fd });
      if (res.ok) {
        onToast('Reference clip uploaded', 'success');
        onDone();
        onClose();
      } else {
        const d = await res.json().catch(() => ({}));
        setErr(d.detail ?? 'Upload failed.');
      }
    } catch {
      setErr('Network error.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}
      style={{ position: 'fixed', inset: 0, zIndex: 150, background: 'rgba(0,0,0,0.85)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px' }}
    >
      <div style={{ background: '#0a0a0a', border: '1px solid #1f1f1f', width: '100%', maxWidth: '460px', padding: '40px', position: 'relative' }}>
        <button onClick={onClose} disabled={busy} style={{ position: 'absolute', top: '16px', right: '16px', background: 'none', border: 'none', color: '#555', fontSize: '18px', cursor: 'pointer' }}>✕</button>
        <div style={{ marginBottom: '24px' }}>
          <div style={{ ...MONO, fontSize: '9px', color: '#444', letterSpacing: '3px', textTransform: 'uppercase', marginBottom: '8px' }}>RIGHTS MANAGER</div>
          <div style={{ fontSize: '18px', fontWeight: 600, color: 'var(--text)' }}>Upload Reference Clip</div>
          <div style={{ width: '32px', height: '1px', background: 'var(--accent)', marginTop: '10px' }} />
        </div>
        <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
          <div>
            <label style={modalLabel}>Reference File</label>
            <label style={{ display: 'block', border: '1px dashed var(--line)', padding: '18px', textAlign: 'center', cursor: 'pointer', ...MONO, fontSize: '11px', color: file ? 'var(--text)' : 'var(--dim)' }}>
              {file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB` : 'Drop video or audio file — mp4, mov, mkv, mp3, wav'}
              <input type="file" accept=".mp4,.mov,.mkv,.mp3,.wav,.m4a,video/*,audio/*" onChange={(e) => setFile(e.target.files?.[0] ?? null)} style={{ display: 'none' }} />
            </label>
          </div>
          <div>
            <label style={modalLabel}>Title</label>
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} style={modalInput} autoFocus />
          </div>
          <div>
            <label style={modalLabel}>Channel</label>
            <select value={channel} onChange={(e) => setChannel(e.target.value)} style={modalInput}>
              {VOICE_CHANNELS.map((c) => <option key={c}>{c}</option>)}
            </select>
          </div>
          <div>
            <label style={modalLabel}>Broadcast Date</label>
            <input type="date" value={broadcastDate} onChange={(e) => setBroadcastDate(e.target.value)} style={modalInput} />
          </div>
          {err && <div style={{ ...MONO, fontSize: '10px', color: 'var(--critical)' }}>✕ {err}</div>}
          <button type="submit" className="btn" disabled={busy} style={{ opacity: busy ? 0.6 : 1 }}>
            {busy ? 'Uploading…' : 'Upload Reference'}
          </button>
        </form>
      </div>
    </div>
  );
}

function CreateStrikeModal({
  refFiles, onClose, onDone, onToast,
}: { refFiles: RefFile[]; onClose: () => void; onDone: () => void; onToast: (m: string, t: 'success' | 'error') => void }) {
  const [platform, setPlatform] = useState('Facebook');
  const [contentUrl, setContentUrl] = useState('');
  const [refId, setRefId] = useState('');
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape' && !busy) onClose(); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose, busy]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    if (!contentUrl.trim()) { setErr('Content URL is required.'); return; }
    setBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/rights-manager/strikes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ platform, content_url: contentUrl.trim(), reference_file_id: refId || null, notes: notes || null }),
      });
      if (res.ok) {
        onToast('Strike draft created — review and file manually', 'success');
        onDone();
        onClose();
      } else {
        const d = await res.json().catch(() => ({}));
        setErr(d.detail ?? 'Failed to create draft.');
      }
    } catch {
      setErr('Network error.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}
      style={{ position: 'fixed', inset: 0, zIndex: 150, background: 'rgba(0,0,0,0.85)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px' }}
    >
      <div style={{ background: '#0a0a0a', border: '1px solid #1f1f1f', width: '100%', maxWidth: '460px', padding: '40px', position: 'relative' }}>
        <button onClick={onClose} disabled={busy} style={{ position: 'absolute', top: '16px', right: '16px', background: 'none', border: 'none', color: '#555', fontSize: '18px', cursor: 'pointer' }}>✕</button>
        <div style={{ marginBottom: '24px' }}>
          <div style={{ ...MONO, fontSize: '9px', color: '#444', letterSpacing: '3px', textTransform: 'uppercase', marginBottom: '8px' }}>STRIKE TRACKER</div>
          <div style={{ fontSize: '18px', fontWeight: 600, color: 'var(--text)' }}>Create Strike Draft</div>
          <div style={{ width: '32px', height: '1px', background: 'var(--accent)', marginTop: '10px' }} />
        </div>
        <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
          <div>
            <label style={modalLabel}>Platform</label>
            <select value={platform} onChange={(e) => setPlatform(e.target.value)} style={modalInput}>
              <option>Facebook</option>
              <option>Instagram</option>
            </select>
          </div>
          <div>
            <label style={modalLabel}>Infringing Content URL</label>
            <input type="text" value={contentUrl} onChange={(e) => setContentUrl(e.target.value)} placeholder="https://…" style={modalInput} autoFocus />
          </div>
          <div>
            <label style={modalLabel}>Reference File (optional)</label>
            <select value={refId} onChange={(e) => setRefId(e.target.value)} style={modalInput}>
              <option value="">— none —</option>
              {refFiles.map((r) => <option key={r.id} value={r.id}>{r.title} · {r.channel}</option>)}
            </select>
          </div>
          <div>
            <label style={modalLabel}>Notes</label>
            <input type="text" value={notes} onChange={(e) => setNotes(e.target.value)} style={modalInput} />
          </div>
          {err && <div style={{ ...MONO, fontSize: '10px', color: 'var(--critical)' }}>✕ {err}</div>}
          <button type="submit" className="btn" disabled={busy} style={{ opacity: busy ? 0.6 : 1 }}>
            {busy ? 'Creating…' : 'Create Draft'}
          </button>
        </form>
      </div>
    </div>
  );
}

function RightsManagerSection() {
  const [refFiles, setRefFiles] = useState<RefFile[]>([]);
  const [strikes, setStrikes] = useState<Strike[]>([]);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [strikeOpen, setStrikeOpen] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);

  function showToast(msg: string, type: 'success' | 'error') {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4000);
  }

  function loadAll() {
    fetch(`${API_URL}/api/rights-manager/reference-files`)
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => { if (Array.isArray(d)) setRefFiles(d); })
      .catch(() => {});
    fetch(`${API_URL}/api/rights-manager/strikes`)
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => { if (Array.isArray(d)) setStrikes(d); })
      .catch(() => {});
  }

  useEffect(() => { loadAll(); }, []);

  const GUIDE_STEPS = [
    'Go to business.facebook.com/rights-manager',
    'Apply for video rights',
    'Upload reference files',
    'Wait for Meta approval (2–4 weeks)',
    'Once approved, upload reference files to Meta manually — Sentinel stores them and tracks strike drafts, but does not submit to Meta',
  ];

  return (
    <div className="admin-section">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px', flexWrap: 'wrap' }}>
        <div>
          <h3>Rights Manager &amp; Strike Tracker</h3>
          <div className="sub">
            Manage broadcast reference clips and copyright strike drafts. Strikes are
            created as DRAFT — a human reviews and files them manually.
          </div>
        </div>
        <div style={{ display: 'flex', gap: '10px', flexShrink: 0 }}>
          <button className="btn" onClick={() => setUploadOpen(true)} style={{ whiteSpace: 'nowrap' }}>
            + Upload Reference Clip
          </button>
        </div>
      </div>

      {/* Reference files table */}
      <div style={{ ...MONO, fontSize: '9px', color: '#555', letterSpacing: '2px', textTransform: 'uppercase', margin: '20px 0 10px' }}>
        Reference Files
      </div>
      <div className="plugin-list">
        {refFiles.length === 0 ? (
          <div className="plugin-row"><div className="plugin-name" style={{ color: 'var(--dim)' }}>No reference files uploaded yet.</div></div>
        ) : refFiles.map((r) => (
          <div className="plugin-row" key={r.id}>
            <div className="plugin-name">
              {r.title} <span className="dim">· {r.channel}</span>
              {r.broadcast_date && <span className="dim"> · aired {r.broadcast_date.slice(0, 10)}</span>}
            </div>
            <div style={{ ...MONO, fontSize: '10px', letterSpacing: '1px', color: r.submitted_to_meta ? 'var(--safe)' : 'var(--dim)' }}>
              {r.submitted_to_meta ? '● SUBMITTED TO META' : '○ NOT SUBMITTED'}
            </div>
          </div>
        ))}
      </div>

      {/* Strike tracker table */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', margin: '24px 0 10px' }}>
        <div style={{ ...MONO, fontSize: '9px', color: '#555', letterSpacing: '2px', textTransform: 'uppercase' }}>
          Strike Tracker
        </div>
        <button
          onClick={() => setStrikeOpen(true)}
          style={{ background: 'transparent', border: '1px solid rgba(255,91,58,0.4)', color: 'var(--accent)', ...MONO, fontSize: '10px', letterSpacing: '1px', padding: '6px 12px', cursor: 'pointer' }}
        >
          + CREATE STRIKE DRAFT
        </button>
      </div>
      <div className="plugin-list">
        {strikes.length === 0 ? (
          <div className="plugin-row"><div className="plugin-name" style={{ color: 'var(--dim)' }}>No strikes yet.</div></div>
        ) : strikes.map((s) => (
          <div className="plugin-row" key={s.id}>
            <div className="plugin-name" style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
              <span className="dim">{s.platform}</span> · {s.content_url}
              {s.filed_at && <span className="dim"> · filed {s.filed_at.slice(0, 10)}</span>}
            </div>
            <div style={{ ...MONO, fontSize: '10px', letterSpacing: '1px', color: STRIKE_STATUS_COLOR[s.status] ?? 'var(--dim)' }}>
              {s.status}
            </div>
          </div>
        ))}
      </div>

      {/* Collapsible guide */}
      <div style={{ marginTop: '20px', borderTop: '1px solid var(--line)', paddingTop: '14px' }}>
        <button
          onClick={() => setGuideOpen((v) => !v)}
          style={{ background: 'transparent', border: 'none', color: 'var(--blue)', ...MONO, fontSize: '10px', letterSpacing: '2px', textTransform: 'uppercase', cursor: 'pointer', padding: 0, display: 'flex', alignItems: 'center', gap: '8px' }}
        >
          <span style={{ width: '10px', display: 'inline-block' }}>{guideOpen ? '▾' : '▸'}</span>
          How to apply for Facebook Rights Manager
        </button>
        {guideOpen && (
          <ol style={{ margin: '14px 0 0', paddingLeft: '20px', display: 'grid', gap: '8px' }}>
            {GUIDE_STEPS.map((step, i) => (
              <li key={i} style={{ ...MONO, fontSize: '12px', color: 'var(--text-mid)', lineHeight: 1.5 }}>{step}</li>
            ))}
          </ol>
        )}
      </div>

      {uploadOpen && <UploadReferenceModal onClose={() => setUploadOpen(false)} onDone={loadAll} onToast={showToast} />}
      {strikeOpen && <CreateStrikeModal refFiles={refFiles} onClose={() => setStrikeOpen(false)} onDone={loadAll} onToast={showToast} />}

      {toast && (
        <div style={{
          position: 'fixed', bottom: '24px', right: '24px', zIndex: 200, padding: '12px 20px',
          background: toast.type === 'success' ? 'rgba(74,222,128,0.12)' : 'rgba(255,56,56,0.12)',
          border: `1px solid ${toast.type === 'success' ? 'rgba(74,222,128,0.4)' : 'rgba(255,56,56,0.4)'}`,
          color: toast.type === 'success' ? 'var(--safe)' : 'var(--critical)',
          ...MONO, fontSize: '11px', letterSpacing: '0.5px',
        }}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}

function VoiceProfilesSection() {
  const [profiles, setProfiles] = useState<VoiceProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [sweeping, setSweeping] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);

  function showToast(msg: string, type: 'success' | 'error') {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4000);
  }

  function loadProfiles() {
    setLoading(true);
    fetch(`${API_URL}/api/voice/profiles`)
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => { if (Array.isArray(d)) setProfiles(d); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }

  useEffect(() => { loadProfiles(); }, []);

  async function onDelete(id: string, name: string) {
    if (!window.confirm(`Delete voice profile for "${name}"? This cannot be undone.`)) return;
    try {
      const res = await fetch(`${API_URL}/api/voice/profiles/${id}`, { method: 'DELETE' });
      if (res.ok) {
        setProfiles((p) => p.filter((x) => x.id !== id));
        showToast('Profile deleted', 'success');
      } else {
        showToast('Delete failed', 'error');
      }
    } catch {
      showToast('Network error', 'error');
    }
  }

  async function onSweep() {
    setSweeping(true);
    try {
      const res = await fetch(`${API_URL}/api/voice/monitor/sweep`, { method: 'POST' });
      if (res.ok) {
        const d = await res.json();
        showToast(
          `Sweep complete — ${d.clips_checked ?? 0} clips checked, ${d.threats_found ?? 0} threats found`,
          'success',
        );
      } else {
        showToast('Sweep failed', 'error');
      }
    } catch {
      showToast('Network error during sweep', 'error');
    } finally {
      setSweeping(false);
    }
  }

  return (
    <div className="admin-section">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px' }}>
        <div>
          <h3>Anchor Voice Profiles</h3>
          <div className="sub">
            Enroll anchor voices for deepfake detection. Upload audio or video — both
            supported.
          </div>
        </div>
        <div style={{ display: 'flex', gap: '10px', flexShrink: 0 }}>
          <button
            className="btn"
            onClick={() => setModalOpen(true)}
            style={{ whiteSpace: 'nowrap' }}
          >
            + Enroll Anchor
          </button>
        </div>
      </div>

      <div className="plugin-list" style={{ marginTop: '16px' }}>
        {loading ? (
          <div className="plugin-row">
            <div className="plugin-name" style={{ color: 'var(--dim)' }}>Loading profiles…</div>
          </div>
        ) : profiles.length === 0 ? (
          <div className="plugin-row">
            <div className="plugin-name" style={{ color: 'var(--dim)' }}>
              No anchors enrolled yet. Click “Enroll Anchor” to add one.
            </div>
          </div>
        ) : (
          profiles.map((p) => (
            <div className="plugin-row" key={p.id}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <div style={{
                  width: '32px', height: '32px', borderRadius: '50%',
                  background: 'rgba(255,91,58,0.12)', border: '1px solid rgba(255,91,58,0.3)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  ...MONO, fontSize: '11px', color: 'var(--accent)', flexShrink: 0,
                }}>
                  {initials(p.anchor_name)}
                </div>
                <div className="plugin-name">
                  {p.anchor_name}{' '}
                  <span className="dim">· {p.channel}</span>{' '}
                  <span className="dim">· {fmtDuration(p.sample_duration_seconds)}</span>{' '}
                  <span style={{
                    ...MONO, fontSize: '9px', letterSpacing: '1px',
                    padding: '2px 6px', marginLeft: '4px',
                    border: '1px solid var(--line)', color: 'var(--dim)',
                    textTransform: 'uppercase',
                  }}>
                    {p.file_type ?? 'audio'}
                  </span>{' '}
                  <span className="dim" style={{ fontSize: '11px' }}>· {fmtDate(p.created_at)}</span>
                </div>
              </div>
              <button
                onClick={() => onDelete(p.id, p.anchor_name)}
                style={{
                  background: 'transparent', border: '1px solid rgba(255,56,56,0.35)',
                  color: 'var(--critical)', ...MONO, fontSize: '10px', letterSpacing: '1px',
                  padding: '5px 12px', cursor: 'pointer',
                }}
              >
                DELETE
              </button>
            </div>
          ))
        )}
      </div>

      <div style={{ marginTop: '14px' }}>
        <button
          onClick={onSweep}
          disabled={sweeping}
          style={{
            background: 'transparent', border: '1px solid var(--line)',
            color: sweeping ? 'var(--dim)' : 'var(--text)', ...MONO,
            fontSize: '11px', letterSpacing: '1px', padding: '9px 16px',
            cursor: sweeping ? 'wait' : 'pointer',
          }}
        >
          {sweeping ? 'Sweeping Telegram channels…' : 'RUN VOICE SWEEP'}
        </button>
      </div>

      {modalOpen && (
        <EnrollModal
          onClose={() => setModalOpen(false)}
          onEnrolled={loadProfiles}
          onToast={showToast}
        />
      )}

      {toast && (
        <div style={{
          position: 'fixed', bottom: '24px', right: '24px', zIndex: 200,
          padding: '12px 20px',
          background: toast.type === 'success' ? 'rgba(74,222,128,0.12)' : 'rgba(255,56,56,0.12)',
          border: `1px solid ${toast.type === 'success' ? 'rgba(74,222,128,0.4)' : 'rgba(255,56,56,0.4)'}`,
          color: toast.type === 'success' ? 'var(--safe)' : 'var(--critical)',
          ...MONO, fontSize: '11px', letterSpacing: '0.5px',
        }}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}

export default function AdminPage() {
  const [killActive, setKillActive]       = useState(false);
  const [killWorking, setKillWorking]     = useState(false);

  const [kaavalCfg, setKaavalCfg]   = useState<KaavalCfg>(KAAVAL_DEFAULTS);
  const [kavasamCfg, setKavasamCfg] = useState<KavasamCfg>(KAVASAM_DEFAULTS);
  const [veriffCfg, setVeriffCfg]   = useState<VeriffCfg>(VERIFF_DEFAULTS);

  const [kaavalSaved, setKaavalSaved]   = useState(false);
  const [kavasamSaved, setKavasamSaved] = useState(false);
  const [veriffSaved, setVeriffSaved]   = useState(false);

  useEffect(() => {
    fetch(`${API_URL}/api/admin/kill-switch`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d) setKillActive(d.active); })
      .catch(() => {});

    async function loadConfigs() {
      const [kR, kavR, vR] = await Promise.allSettled([
        fetch(`${API_URL}/api/config/kaaval`).then((r) => (r.ok ? r.json() : null)),
        fetch(`${API_URL}/api/config/kavasam`).then((r) => (r.ok ? r.json() : null)),
        fetch(`${API_URL}/api/config/veriff`).then((r) => (r.ok ? r.json() : null)),
      ]);
      if (kR.status === 'fulfilled' && kR.value && Object.keys(kR.value).length > 0) {
        setKaavalCfg((prev) => ({ ...prev, ...kR.value }));
      }
      if (kavR.status === 'fulfilled' && kavR.value && Object.keys(kavR.value).length > 0) {
        setKavasamCfg((prev) => ({ ...prev, ...kavR.value }));
      }
      if (vR.status === 'fulfilled' && vR.value && Object.keys(vR.value).length > 0) {
        setVeriffCfg((prev) => ({ ...prev, ...vR.value }));
      }
    }
    loadConfigs().catch(() => {});
  }, []);

  async function toggleKillSwitch() {
    setKillWorking(true);
    try {
      const action = killActive ? 'resume' : 'pause';
      const res = await fetch(`${API_URL}/api/admin/kill-switch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      });
      if (res.ok) setKillActive(!killActive);
    } catch {
      // silently ignore network errors
    } finally {
      setKillWorking(false);
    }
  }

  async function saveKaaval() {
    try {
      await fetch(`${API_URL}/api/config/kaaval`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(kaavalCfg),
      });
    } catch { /* ignore */ }
    setKaavalSaved(true);
    setTimeout(() => setKaavalSaved(false), 3000);
  }

  async function saveKavasam() {
    try {
      await fetch(`${API_URL}/api/config/kavasam`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(kavasamCfg),
      });
    } catch { /* ignore */ }
    setKavasamSaved(true);
    setTimeout(() => setKavasamSaved(false), 3000);
  }

  async function saveVeriff() {
    try {
      await fetch(`${API_URL}/api/config/veriff`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(veriffCfg),
      });
    } catch { /* ignore */ }
    setVeriffSaved(true);
    setTimeout(() => setVeriffSaved(false), 3000);
  }

  return (
    <div className="page" id="page-admin">
      <div className="page-header">
        <div className="page-label">SUPER ADMIN CONSOLE</div>
        <h1 className="page-title med">
          Configurable everything.
          <br />
          <span className="accent">Nothing hardcoded.</span>
        </h1>
        <p className="page-sub">
          Every threshold, every API key, every agent prompt, every alert rule lives in
          the database — controlled from this UI. No developer required for
          day-to-day changes.
        </p>
      </div>

      <div className="admin-section">
        <h3>Kaaval AI Configuration</h3>
        <div className="sub">
          Keywords, channel IDs, deepfake sensitivity, alert frequency, whitelists. All
          editable here.
        </div>
        <div className="form-row">
          <div className="form-field">
            <label>Deepfake Sensitivity (0-100)</label>
            <input
              type="text"
              value={kaavalCfg.deepfake_sensitivity}
              onChange={(e) => setKaavalCfg((p) => ({ ...p, deepfake_sensitivity: e.target.value }))}
            />
          </div>
          <div className="form-field">
            <label>Alert Frequency</label>
            <select
              value={kaavalCfg.alert_frequency}
              onChange={(e) => setKaavalCfg((p) => ({ ...p, alert_frequency: e.target.value }))}
            >
              <option>Real-time</option>
              <option>Hourly batch</option>
              <option>Daily digest</option>
            </select>
          </div>
        </div>
        <div className="form-row">
          <div className="form-field">
            <label>Monitored Keywords (Tamil)</label>
            <input
              type="text"
              value={kaavalCfg.keywords_tamil}
              onChange={(e) => setKaavalCfg((p) => ({ ...p, keywords_tamil: e.target.value }))}
            />
          </div>
          <div className="form-field">
            <label>Monitored Keywords (English)</label>
            <input
              type="text"
              value={kaavalCfg.keywords_english}
              onChange={(e) => setKaavalCfg((p) => ({ ...p, keywords_english: e.target.value }))}
            />
          </div>
        </div>
        <div style={{ marginTop: '12px', display: 'flex', alignItems: 'center' }}>
          <button className="btn" onClick={saveKaaval}>Save Kaaval Config</button>
          <SavedBadge visible={kaavalSaved} />
        </div>
      </div>

      <div className="admin-section">
        <h3>Digital Kavasam Configuration</h3>
        <div className="sub">
          YouTube OAuth, anomaly thresholds, scan intervals, escalation chain. CERT-In
          contact pre-filled.
        </div>
        <div className="form-row">
          <div className="form-field">
            <label>Anomaly Threshold</label>
            <input
              type="text"
              value={kavasamCfg.anomaly_threshold}
              onChange={(e) => setKavasamCfg((p) => ({ ...p, anomaly_threshold: e.target.value }))}
            />
          </div>
          <div className="form-field">
            <label>Scan Interval</label>
            <select
              value={kavasamCfg.scan_interval}
              onChange={(e) => setKavasamCfg((p) => ({ ...p, scan_interval: e.target.value }))}
            >
              <option>Every 5 minutes</option>
              <option>Every 15 minutes</option>
              <option>Every hour</option>
            </select>
          </div>
        </div>
        <div className="form-row">
          <div className="form-field">
            <label>CERT-In Contact Email</label>
            <input
              type="text"
              value={kavasamCfg.certin_contact_email}
              onChange={(e) => setKavasamCfg((p) => ({ ...p, certin_contact_email: e.target.value }))}
            />
          </div>
          <div className="form-field">
            <label>Bulk Deletion Threshold</label>
            <input
              type="text"
              value={kavasamCfg.bulk_deletion_threshold}
              onChange={(e) => setKavasamCfg((p) => ({ ...p, bulk_deletion_threshold: e.target.value }))}
            />
          </div>
        </div>
        <div style={{ marginTop: '12px', display: 'flex', alignItems: 'center' }}>
          <button className="btn" onClick={saveKavasam}>Save Kavasam Config</button>
          <SavedBadge visible={kavasamSaved} />
        </div>
      </div>

      <div className="admin-section">
        <h3>Veriff Configuration</h3>
        <div className="sub">
          Confidence thresholds, languages, deepfake sensitivity. To enable or
          disable individual fact-check sources, use the{' '}
          <a href="/plugins" style={{ color: 'var(--accent)' }}>Plugin Marketplace</a>{' '}
          — those toggles persist and are read by the scrapers at scan time.
        </div>
        <div className="form-row">
          <div className="form-field">
            <label>Verdict Mode</label>
            <select
              value={veriffCfg.verdict_mode}
              onChange={(e) => setVeriffCfg((p) => ({ ...p, verdict_mode: e.target.value }))}
            >
              <option>Auto + Human review</option>
              <option>Auto-only</option>
              <option>Human-only</option>
            </select>
          </div>
          <div className="form-field">
            <label>Confidence Threshold for Auto-Verdict</label>
            <input
              type="text"
              value={veriffCfg.confidence_threshold}
              onChange={(e) => setVeriffCfg((p) => ({ ...p, confidence_threshold: e.target.value }))}
            />
          </div>
        </div>
        <div style={{ marginTop: '12px', display: 'flex', alignItems: 'center' }}>
          <button className="btn" onClick={saveVeriff}>Save Veriff Config</button>
          <SavedBadge visible={veriffSaved} />
        </div>
      </div>

      <div className="admin-section">
        <h3>API Key Vault</h3>
        <div className="sub">
          All third-party API credentials, encrypted at rest, rotatable from one
          screen.
        </div>
        <div className="plugin-list">
          <div className="plugin-row">
            <div className="plugin-name">
              YouTube Data API v3 <span className="dim">· Last rotated 14 days ago</span>
            </div>
            <div
              style={{
                fontFamily: "'Space Mono', monospace",
                fontSize: '11px',
                color: 'var(--safe)',
              }}
            >
              ● ACTIVE
            </div>
          </div>
          <div className="plugin-row">
            <div className="plugin-name">
              Telegram Bot API <span className="dim">· Last rotated 7 days ago</span>
            </div>
            <div
              style={{
                fontFamily: "'Space Mono', monospace",
                fontSize: '11px',
                color: 'var(--safe)',
              }}
            >
              ● ACTIVE
            </div>
          </div>
          <div className="plugin-row">
            <div className="plugin-name">
              Groq API <span className="dim">· Last rotated 2 days ago</span>
            </div>
            <div
              style={{
                fontFamily: "'Space Mono', monospace",
                fontSize: '11px',
                color: 'var(--safe)',
              }}
            >
              ● ACTIVE
            </div>
          </div>
          <div className="plugin-row">
            <div className="plugin-name">
              PIB Fact-Check API <span className="dim">· Rotation due in 4 days</span>
            </div>
            <div
              style={{
                fontFamily: "'Space Mono', monospace",
                fontSize: '11px',
                color: 'var(--high)',
              }}
            >
              ▲ ROTATE
            </div>
          </div>
        </div>
      </div>

      <YoutubeMonitoringSection />

      <MetaIntegrationSection />

      <RightsManagerSection />

      <VoiceProfilesSection />

      <div className="kill-zone">
        <h3>MASTER KILL SWITCH</h3>
        <div className="desc">
          Instantly pause every agent across all three modules. One click. No
          developer needed. All state preserved for resumption.
        </div>
        {killActive && (
          <div style={{
            fontFamily: "'Space Mono', monospace",
            fontSize: '11px',
            color: 'var(--safe)',
            marginBottom: '16px',
            letterSpacing: '1px',
          }}>
            ● ALL AGENTS PAUSED
          </div>
        )}
        <button
          className="kill-btn"
          disabled={killWorking}
          onClick={toggleKillSwitch}
          style={killActive ? {
            background: 'var(--safe)',
            color: '#000',
            opacity: killWorking ? 0.6 : 1,
          } : {
            opacity: killWorking ? 0.6 : 1,
          }}
        >
          {killWorking
            ? '...'
            : killActive
              ? '▶ RESUME ALL AGENTS'
              : '⏸ PAUSE ALL AGENTS'}
        </button>
      </div>
    </div>
  );
}

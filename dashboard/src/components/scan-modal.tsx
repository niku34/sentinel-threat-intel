'use client';

import { useState } from 'react';

type ScanModalProps = {
  onClose: () => void;
};

const TARGETS = [
  { value: 'all', label: 'All Channels' },
  { value: 'brand_main', label: 'Example Broadcaster' },
  { value: 'brand_news', label: 'Example News' },
  { value: 'brand_entertainment', label: 'Example Entertainment' },
  { value: 'brand_movies', label: 'Example Movies' },
];

const SCAN_TYPES = [
  { value: 'full', label: 'Full Sweep' },
  { value: 'quick', label: 'Quick Check' },
];

export function ScanModal({ onClose }: ScanModalProps) {
  const [target, setTarget] = useState('all');
  const [scanType, setScanType] = useState('full');
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);

  async function handleStart() {
    setLoading(true);
    setToast(null);
    try {
      const res = await fetch('http://localhost:8000/api/scan/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target, scan_type: scanType }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? 'Failed to start scan');
      setToast({ msg: data.message, ok: true });
      setTimeout(() => {
        setToast(null);
        onClose();
      }, 2000);
    } catch (err: unknown) {
      setToast({
        msg: err instanceof Error ? err.message : 'Error starting scan',
        ok: false,
      });
      setLoading(false);
    }
  }

  return (
    <>
      <div
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(0,0,0,0.6)',
          zIndex: 999,
        }}
        onClick={onClose}
      />
      <div
        style={{
          position: 'fixed',
          top: '50%',
          left: '50%',
          transform: 'translate(-50%, -50%)',
          zIndex: 1000,
          background: 'var(--bg-card)',
          border: '1px solid var(--line-bright)',
          borderRadius: '6px',
          width: '360px',
          boxShadow: '0 8px 40px rgba(0,0,0,0.6)',
        }}
      >
        {/* header */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            padding: '16px 20px',
            borderBottom: '1px solid var(--line)',
          }}
        >
          <span
            style={{
              fontFamily: 'var(--font-bebas-neue), "Bebas Neue", sans-serif',
              fontSize: '18px',
              letterSpacing: '2px',
              color: 'var(--text)',
            }}
          >
            NEW SCAN
          </span>
          <button
            type="button"
            onClick={onClose}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--text-mid)',
              cursor: 'pointer',
              fontSize: '14px',
              lineHeight: 1,
            }}
          >
            ✕
          </button>
        </div>

        {/* body */}
        <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <label
              style={{
                fontSize: '11px',
                fontFamily: 'var(--font-space-mono), monospace',
                color: 'var(--text-mid)',
                textTransform: 'uppercase',
                letterSpacing: '1px',
              }}
            >
              Target
            </label>
            <select
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              style={{
                background: 'var(--bg)',
                border: '1px solid var(--line-bright)',
                borderRadius: '4px',
                color: 'var(--text)',
                padding: '8px 10px',
                fontSize: '13px',
                fontFamily: 'inherit',
                cursor: 'pointer',
                outline: 'none',
              }}
            >
              {TARGETS.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <label
              style={{
                fontSize: '11px',
                fontFamily: 'var(--font-space-mono), monospace',
                color: 'var(--text-mid)',
                textTransform: 'uppercase',
                letterSpacing: '1px',
              }}
            >
              Scan Type
            </label>
            <select
              value={scanType}
              onChange={(e) => setScanType(e.target.value)}
              style={{
                background: 'var(--bg)',
                border: '1px solid var(--line-bright)',
                borderRadius: '4px',
                color: 'var(--text)',
                padding: '8px 10px',
                fontSize: '13px',
                fontFamily: 'inherit',
                cursor: 'pointer',
                outline: 'none',
              }}
            >
              {SCAN_TYPES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* footer */}
        <div
          style={{
            padding: '0 20px 20px',
            display: 'flex',
            flexDirection: 'column',
            gap: '10px',
          }}
        >
          <button
            type="button"
            className="btn"
            onClick={handleStart}
            disabled={loading}
            style={{ width: '100%', justifyContent: 'center', opacity: loading ? 0.6 : 1 }}
          >
            {loading ? 'STARTING…' : 'START SCAN'}
          </button>

          {toast && (
            <div
              style={{
                padding: '8px 12px',
                borderRadius: '4px',
                fontSize: '12px',
                fontFamily: 'var(--font-space-mono), monospace',
                background: toast.ok ? 'rgba(74,222,128,0.1)' : 'rgba(255,56,56,0.1)',
                border: `1px solid ${toast.ok ? 'var(--safe)' : 'var(--critical)'}`,
                color: toast.ok ? 'var(--safe)' : 'var(--critical)',
                textAlign: 'center',
              }}
            >
              {toast.msg}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

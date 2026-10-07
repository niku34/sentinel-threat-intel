'use client';

import { useEffect, useState } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
const MONO: React.CSSProperties = { fontFamily: "'Space Mono', monospace" };

// ── Types ──────────────────────────────────────────────────────────────────

interface Plugin {
  id: string;
  name: string;
  module: string;
  enabled: boolean;
  version: string;
  updated_at: string;
}

// ── Module config ──────────────────────────────────────────────────────────

const MODULE_LABEL: Record<string, string> = {
  kaaval:  'KAAVAL AI',
  kavasam: 'DIGITAL KAVASAM',
  veriff:  'VERIFF TAMIL',
};

const MODULE_CLASS: Record<string, string> = {
  kaaval:  'kaaval',
  kavasam: 'kavasam',
  veriff:  'veriff',
};

const MODULE_ORDER = ['kaaval', 'kavasam', 'veriff'];

// ── Toast ──────────────────────────────────────────────────────────────────

function Toast({ message, type }: { message: string; type: 'success' | 'error' }) {
  return (
    <div style={{
      position: 'fixed', bottom: '24px', right: '24px', zIndex: 200,
      padding: '12px 20px',
      background: type === 'success' ? 'rgba(74,222,128,0.12)' : 'rgba(255,56,56,0.12)',
      border: `1px solid ${type === 'success' ? 'rgba(74,222,128,0.4)' : 'rgba(255,56,56,0.4)'}`,
      color: type === 'success' ? 'var(--safe)' : 'var(--critical)',
      ...MONO, fontSize: '11px', letterSpacing: '0.5px',
    }}>
      {message}
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function PluginsPage() {
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [loading, setLoading]   = useState(true);
  const [toggling, setToggling] = useState<Record<string, boolean>>({});
  const [toast, setToast]       = useState<{ message: string; type: 'success' | 'error' } | null>(null);

  function showToast(message: string, type: 'success' | 'error') {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  }

  useEffect(() => {
    fetch(`${API_URL}/api/plugins`)
      .then((r) => r.json())
      .then((d) => setPlugins(d.plugins ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  async function toggle(plugin: Plugin) {
    setToggling((t) => ({ ...t, [plugin.id]: true }));
    try {
      const res  = await fetch(`${API_URL}/api/plugins/${plugin.id}/toggle`, { method: 'PATCH' });
      const data = await res.json();
      if (!res.ok) { showToast(data.detail ?? 'Toggle failed.', 'error'); return; }
      setPlugins((prev) =>
        prev.map((p) => p.id === plugin.id ? { ...p, enabled: data.enabled } : p)
      );
      showToast(`${plugin.name} ${data.enabled ? 'enabled' : 'disabled'}.`, 'success');
    } catch {
      showToast('Network error.', 'error');
    } finally {
      setToggling((t) => ({ ...t, [plugin.id]: false }));
    }
  }

  // Metrics
  const total    = plugins.length;
  const enabled  = plugins.filter((p) => p.enabled).length;
  const disabled = total - enabled;

  // Group by module in defined order
  const byModule: Record<string, Plugin[]> = {};
  for (const p of plugins) {
    if (!byModule[p.module]) byModule[p.module] = [];
    byModule[p.module].push(p);
  }

  return (
    <div className="page" id="page-plugins">
      <div className="page-header">
        <div className="page-label">PLUGIN MARKETPLACE</div>
        <h1 className="page-title med">
          Extend every module.
          <br />
          <span className="accent">One toggle. Live.</span>
        </h1>
        <p className="page-sub">
          Each plugin extends a core module — add scrapers, connectors, NLP models,
          and compliance tools without touching the platform itself. Enable or disable
          at runtime with instant effect.
        </p>
      </div>

      <div className="metrics">
        <div className="metric">
          <div className="metric-label">Installed</div>
          <div className="metric-value accent">{loading ? '…' : total}</div>
          <div className="metric-delta">Total plugins</div>
        </div>
        <div className="metric">
          <div className="metric-label">Enabled</div>
          <div className="metric-value safe">{loading ? '…' : enabled}</div>
          <div className="metric-delta safe">Active right now</div>
        </div>
        <div className="metric">
          <div className="metric-label">Disabled</div>
          <div className="metric-value">{loading ? '…' : disabled}</div>
          <div className="metric-delta">Inactive</div>
        </div>
        <div className="metric">
          <div className="metric-label">Modules</div>
          <div className="metric-value">3</div>
          <div className="metric-delta">Kaaval · Kavasam · Veriff</div>
        </div>
      </div>

      {loading && (
        <div className="panel">
          <div className="panel-body" style={{ padding: '24px', textAlign: 'center' }}>
            <span style={{ ...MONO, fontSize: '11px', color: 'var(--dim)' }}>Loading plugins…</span>
          </div>
        </div>
      )}

      {!loading && MODULE_ORDER.map((module) => {
        const list = byModule[module] ?? [];
        if (list.length === 0) return null;
        const moduleEnabled = list.filter((p) => p.enabled).length;
        return (
          <div className="panel" key={module} style={{ marginBottom: '24px' }}>
            <div className="panel-header">
              <div className={`panel-title ${MODULE_CLASS[module]}`}>
                {MODULE_LABEL[module]}
              </div>
              <div className="panel-tag">
                {moduleEnabled} / {list.length} enabled
              </div>
            </div>
            <div className="panel-body" style={{ padding: 0 }}>
              <div className="plugin-list">
                {list.map((plugin) => (
                  <div className="plugin-row" key={plugin.id}>
                    <div className="plugin-name">
                      {plugin.name}{' '}
                      <span className="dim">· {plugin.version}</span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
                      <span style={{
                        ...MONO, fontSize: '10px', letterSpacing: '1px',
                        color: plugin.enabled ? 'var(--safe)' : 'var(--dim)',
                        minWidth: '52px', textAlign: 'right',
                        opacity: toggling[plugin.id] ? 0.4 : 1,
                        transition: 'opacity 0.15s',
                      }}>
                        {plugin.enabled ? 'ENABLED' : 'OFF'}
                      </span>
                      <button
                        className={`toggle${plugin.enabled ? ' on' : ''}`}
                        onClick={() => toggle(plugin)}
                        disabled={toggling[plugin.id]}
                        style={{
                          border: 'none', padding: 0, flexShrink: 0,
                          opacity: toggling[plugin.id] ? 0.5 : 1,
                          cursor: toggling[plugin.id] ? 'wait' : 'pointer',
                        }}
                        aria-label={`${plugin.enabled ? 'Disable' : 'Enable'} ${plugin.name}`}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        );
      })}

      {toast && <Toast message={toast.message} type={toast.type} />}
    </div>
  );
}

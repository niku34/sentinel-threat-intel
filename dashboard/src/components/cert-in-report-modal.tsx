'use client';

// Shared CERT-In draft detail modal — originally built for the Findings page,
// reused as-is by Kavasam's anomaly detail view so both surfaces show the
// exact same report layout for the exact same underlying record
// (GET /api/certin/drafts / /api/certin/drafts/{reference_number}).

import { useEffect, useRef } from 'react';

export interface CertInReport {
  reporting_to: string;
  deadline: {
    deadline_at: string;
    status: 'WITHIN_WINDOW' | 'DEADLINE_PASSED';
    minutes_remaining: number;
  };
  organisation: {
    name: string;
    contact_email: string;
    point_of_contact: string;
  };
  mandatory_fields: {
    nature_of_incident: string;
    date_and_time_of_detection: string;
    service_affected: string;
    brief_description: string;
    impact_on_organisation: string;
    actions_taken: string;
    remedial_actions_planned: string;
  };
}

export interface CertInDraft {
  id: string;
  reference_number: string;
  incident_type: string;
  affected_system: string;
  detected_at: string;
  report: CertInReport | null;
  status: string;
  // Top-level columns present on every draft (both the generic
  // POST /api/certin/draft path and Kavasam's auto-draft trigger set
  // these), independently of whether report.mandatory_fields was populated —
  // Kavasam's auto-drafts don't set mandatory_fields, so these are the real
  // fallback, not a placeholder.
  description?: string;
  impact?: string;
}

export function fmtDetected(iso: string): string {
  return new Intl.DateTimeFormat('en-IN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(iso));
}

export const MONO: React.CSSProperties = { fontFamily: "'Space Mono', monospace" };
export const LABEL_STYLE: React.CSSProperties = { ...MONO, fontSize: '9px', color: '#555', letterSpacing: '2px', textTransform: 'uppercase', marginBottom: '4px' };
export const VALUE_STYLE: React.CSSProperties = { fontSize: '13px', color: '#d0d0d0', lineHeight: 1.5, marginBottom: '20px' };

export function Field({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div>
      <div style={LABEL_STYLE}>{label}</div>
      <div style={{ ...VALUE_STYLE, ...(accent ? { color: 'var(--accent)' } : {}) }}>{value}</div>
    </div>
  );
}

export function CertInReportModal({ draft, onClose }: { draft: CertInDraft; onClose: () => void }) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const r = draft.report;
  const mf = r?.mandatory_fields;
  const deadline = r?.deadline;
  const withinWindow = deadline?.status === 'WITHIN_WINDOW';

  function handleOverlayClick(e: React.MouseEvent) {
    if (e.target === overlayRef.current) onClose();
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') onClose(); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      ref={overlayRef}
      onClick={handleOverlayClick}
      style={{
        position: 'fixed', inset: 0, zIndex: 50,
        background: 'rgba(0,0,0,0.9)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '24px',
        overflowY: 'auto',
      }}
    >
      <div style={{
        background: '#0a0a0a',
        border: '1px solid #1f1f1f',
        width: '100%', maxWidth: '640px',
        maxHeight: '90vh',
        overflowY: 'auto',
        position: 'relative',
        padding: '40px',
      }}>
        {/* close */}
        <button
          onClick={onClose}
          style={{
            position: 'absolute', top: '16px', right: '16px',
            background: 'none', border: 'none',
            color: '#555', fontSize: '20px', cursor: 'pointer', lineHeight: 1,
            padding: '4px 8px',
          }}
        >
          ✕
        </button>

        {/* header */}
        <div style={{ marginBottom: '32px' }}>
          <div style={{ ...MONO, fontSize: '9px', color: '#444', letterSpacing: '3px', textTransform: 'uppercase', marginBottom: '8px' }}>
            CERT-IN DRAFT REPORT
          </div>
          <div style={{ ...MONO, fontSize: '18px', color: 'var(--accent)', letterSpacing: '1px' }}>
            {draft.reference_number}
          </div>
          <div style={{ width: '40px', height: '1px', background: 'var(--accent)', marginTop: '12px' }} />
        </div>

        {/* mandatory fields */}
        <Field label="Nature of Incident" value={mf?.nature_of_incident ?? draft.incident_type} />
        <Field label="Date / Time of Detection" value={mf?.date_and_time_of_detection ? fmtDetected(mf.date_and_time_of_detection) : fmtDetected(draft.detected_at)} />
        <Field label="Affected System" value={mf?.service_affected ?? draft.affected_system} />
        <Field label="Brief Description" value={mf?.brief_description ?? draft.description ?? '—'} />
        <Field label="Impact on Organisation" value={mf?.impact_on_organisation ?? draft.impact ?? '—'} />
        <Field label="Actions Taken" value={mf?.actions_taken ?? '—'} />
        <Field label="Remedial Actions Planned" value={mf?.remedial_actions_planned ?? '—'} />

        {/* org */}
        <div style={{ borderTop: '1px solid #1a1a1a', paddingTop: '20px', marginTop: '4px' }}>
          <Field label="Organisation" value={r?.organisation?.name ?? 'Example Broadcaster'} />
          <Field label="Contact Email" value={r?.organisation?.contact_email ?? '—'} />
          <Field label="Reporting To" value={r?.reporting_to ?? 'incident@cert-in.org.in'} accent />
        </div>

        {/* deadline */}
        <div style={{ borderTop: '1px solid #1a1a1a', paddingTop: '20px', marginTop: '4px' }}>
          <div style={LABEL_STYLE}>Deadline Status</div>
          <div style={{ ...VALUE_STYLE, color: withinWindow ? 'var(--safe)' : 'var(--accent)' }}>
            {deadline?.status ?? '—'}
            {withinWindow && deadline && (
              <span style={{ color: '#888', marginLeft: '12px' }}>
                {deadline.minutes_remaining} min remaining
              </span>
            )}
          </div>
        </div>

        {/* file button */}
        <a
          href="mailto:incident@cert-in.org.in"
          style={{
            display: 'block', marginTop: '8px',
            padding: '12px 20px',
            background: 'rgba(255,91,58,0.1)',
            border: '1px solid rgba(255,91,58,0.4)',
            color: 'var(--accent)',
            ...MONO,
            fontSize: '11px',
            letterSpacing: '1px',
            textDecoration: 'none',
            textAlign: 'center',
          }}
        >
          READY TO FILE → incident@cert-in.org.in
        </a>
      </div>
    </div>
  );
}

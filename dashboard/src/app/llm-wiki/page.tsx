'use client';

import { useEffect, useRef, useState } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

const MONO: React.CSSProperties = { fontFamily: "'Space Mono', monospace" };

interface Message {
  role: 'user' | 'assistant';
  content: string;
  offline?: boolean;
}

const SUGGESTED: string[] = [
  'What is CERT-In and what are the 6-hour reporting rules?',
  'How does Kaaval AI detect deepfakes?',
  'Explain the DPDP Act 2023 obligations for Example Broadcaster.',
  'What brigade attack patterns does Kaaval watch for?',
  'How does Veriff cross-reference Tamil fact claims?',
  'What is a DMCA takedown and when should we file one?',
];

export default function LLMWikiPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [model, setModel] = useState('qwen2.5:14b');
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  function retry(index: number) {
    const text = messages[index].content;
    const truncated = messages.slice(0, index);
    setMessages(truncated);
    send(text, truncated);
  }

  function edit(index: number) {
    setInput(messages[index].content);
    setMessages((prev) => prev.slice(0, index));
    setTimeout(() => inputRef.current?.focus(), 50);
  }

  async function send(text: string, base?: Message[]) {
    const trimmed = text.trim();
    if (!trimmed || loading) return;

    const prior = base ?? messages;
    const userMsg: Message = { role: 'user', content: trimmed };
    const nextMessages = [...prior, userMsg].slice(-10);
    setMessages(nextMessages);
    setInput('');
    setLoading(true);

    try {
      const history = nextMessages
        .slice(0, -1)
        .map((m) => ({ role: m.role, content: m.content }));

      const res = await fetch(`${API_URL}/api/llm-wiki/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: trimmed, history }),
      });

      const data = await res.json();
      setModel(data.model ?? 'qwen2.5:14b');
      const offline = typeof data.response === 'string' && data.response.includes('offline');
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: data.response, offline },
      ]);
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: 'Assistant unavailable — Ollama not responding',
          offline: true,
        },
      ]);
    } finally {
      setLoading(false);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }

  function handleKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  const isOffline = model === 'offline';

  return (
    <div className="page" id="page-llm-wiki" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Page header */}
      <div className="page-header" style={{ flexShrink: 0 }}>
        <div className="page-label">INTELLIGENCE · LLM WIKI</div>
        <h1 className="page-title">
          அறிவு
          <br />
          <span className="accent">Ask Sentinel.</span>
        </h1>
        <p className="page-sub">
          Locally-running AI assistant powered by Qwen 2.5 14B — knows Sentinel
          modules, Indian cybersecurity law, Tamil broadcast threat patterns, and CERT-In
          compliance. Runs entirely on-premise. No data leaves the network.
        </p>
      </div>

      {/* Chat container */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '0', minHeight: 0 }}>

        {/* Assistant identity bar */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '12px 20px', borderTop: '1px solid var(--line)', borderBottom: '1px solid var(--line)',
          background: 'rgba(255,255,255,0.02)', flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <div style={{
              width: '28px', height: '28px', borderRadius: '50%',
              background: 'rgba(255,91,58,0.15)', border: '1px solid rgba(255,91,58,0.3)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              ...MONO, fontSize: '11px', color: 'var(--accent)',
            }}>
              ⊕
            </div>
            <div>
              <div style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text)' }}>
                Sentinel Assistant
              </div>
              <div style={{
                ...MONO, fontSize: '9px', letterSpacing: '1px',
                color: isOffline ? 'var(--critical)' : 'var(--safe)',
              }}>
                {isOffline ? 'OFFLINE' : `${model} · local`}
              </div>
            </div>
          </div>
          <div style={{
            ...MONO, fontSize: '9px', color: '#444', letterSpacing: '1px',
          }}>
            ON-PREMISE · NO DATA LEAVES NETWORK
          </div>
        </div>

        {/* Message area */}
        <div style={{
          flex: 1, overflowY: 'auto', padding: '24px 0',
          display: 'flex', flexDirection: 'column', gap: '20px',
          minHeight: '320px', maxHeight: '520px',
        }}>
          {messages.length === 0 && (
            <div style={{ padding: '0 20px' }}>
              <div style={{ ...MONO, fontSize: '9px', color: '#444', letterSpacing: '2px', marginBottom: '16px' }}>
                SUGGESTED QUESTIONS
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {SUGGESTED.map((q) => (
                  <button
                    key={q}
                    onClick={() => send(q)}
                    style={{
                      textAlign: 'left', background: 'transparent',
                      border: '1px solid var(--line)', padding: '10px 14px',
                      cursor: 'pointer', color: 'var(--text-mid)',
                      fontSize: '12px', lineHeight: 1.4,
                      transition: 'border-color 0.15s, color 0.15s',
                    }}
                    onMouseEnter={(e) => {
                      (e.currentTarget as HTMLElement).style.borderColor = 'rgba(255,91,58,0.4)';
                      (e.currentTarget as HTMLElement).style.color = 'var(--text)';
                    }}
                    onMouseLeave={(e) => {
                      (e.currentTarget as HTMLElement).style.borderColor = 'var(--line)';
                      (e.currentTarget as HTMLElement).style.color = 'var(--text-mid)';
                    }}
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) => (
            <div
              key={i}
              className="msg-row"
              style={{
                padding: '0 20px',
                display: 'flex',
                flexDirection: 'column',
                alignItems: m.role === 'user' ? 'flex-end' : 'flex-start',
              }}
            >
              <div style={{ display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start', width: '100%' }}>
                {m.role === 'assistant' && (
                  <div style={{
                    width: '22px', height: '22px', borderRadius: '50%', flexShrink: 0,
                    background: 'rgba(255,91,58,0.15)', border: '1px solid rgba(255,91,58,0.3)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    ...MONO, fontSize: '9px', color: 'var(--accent)',
                    marginRight: '10px', marginTop: '2px',
                  }}>
                    ⊕
                  </div>
                )}
                <div style={{
                  maxWidth: '78%',
                  padding: '12px 16px',
                  background: m.role === 'user'
                    ? 'rgba(255,91,58,0.1)'
                    : m.offline
                    ? 'rgba(255,56,56,0.08)'
                    : 'rgba(255,255,255,0.04)',
                  border: `1px solid ${
                    m.role === 'user'
                      ? 'rgba(255,91,58,0.3)'
                      : m.offline
                      ? 'rgba(255,56,56,0.3)'
                      : 'var(--line)'
                  }`,
                  fontSize: '13px',
                  lineHeight: 1.6,
                  color: m.offline ? 'var(--critical)' : 'var(--text)',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}>
                  {m.content}
                </div>
              </div>

              {/* Retry + Edit — user messages only */}
              {m.role === 'user' && (
                <div className="msg-actions" style={{
                  display: 'flex', gap: '8px', marginTop: '4px',
                  opacity: 0, transition: 'opacity 0.15s',
                }}>
                  <button
                    onClick={() => retry(i)}
                    disabled={loading}
                    style={{
                      background: 'none', border: 'none', cursor: loading ? 'not-allowed' : 'pointer',
                      ...MONO, fontSize: '9px', letterSpacing: '1px',
                      color: 'var(--dim)', padding: '2px 0',
                    }}
                  >
                    ↺ Retry
                  </button>
                  <span style={{ ...MONO, fontSize: '9px', color: '#333' }}>·</span>
                  <button
                    onClick={() => edit(i)}
                    disabled={loading}
                    style={{
                      background: 'none', border: 'none', cursor: loading ? 'not-allowed' : 'pointer',
                      ...MONO, fontSize: '9px', letterSpacing: '1px',
                      color: 'var(--dim)', padding: '2px 0',
                    }}
                  >
                    ✎ Edit
                  </button>
                </div>
              )}
            </div>
          ))}

          {loading && (
            <div style={{ padding: '0 20px', display: 'flex', alignItems: 'center', gap: '10px' }}>
              <div style={{
                width: '22px', height: '22px', borderRadius: '50%', flexShrink: 0,
                background: 'rgba(255,91,58,0.15)', border: '1px solid rgba(255,91,58,0.3)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                ...MONO, fontSize: '9px', color: 'var(--accent)',
              }}>
                ⊕
              </div>
              <div style={{
                padding: '12px 16px',
                background: 'rgba(255,255,255,0.04)', border: '1px solid var(--line)',
                display: 'flex', alignItems: 'center', gap: '8px',
              }}>
                <span style={{ ...MONO, fontSize: '11px', color: 'var(--dim)' }}>Thinking</span>
                <span style={{ display: 'flex', gap: '3px' }}>
                  {[0, 1, 2].map((i) => (
                    <span
                      key={i}
                      style={{
                        width: '4px', height: '4px', borderRadius: '50%',
                        background: 'var(--accent)',
                        display: 'inline-block',
                        animation: `pulse 1.2s ease-in-out ${i * 0.2}s infinite`,
                      }}
                    />
                  ))}
                </span>
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        {/* Input row */}
        <div style={{
          flexShrink: 0, borderTop: '1px solid var(--line)',
          padding: '16px 20px', display: 'flex', gap: '10px', alignItems: 'flex-end',
        }}>
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Ask about threats, compliance, modules… (Enter to send, Shift+Enter for newline)"
            rows={2}
            style={{
              flex: 1, resize: 'none',
              background: 'var(--bg)', border: '1px solid var(--line)',
              color: 'var(--text)', ...MONO, fontSize: '12px',
              padding: '10px 14px', outline: 'none', lineHeight: 1.5,
              fontFamily: 'inherit',
            }}
            onFocus={(e) => { e.currentTarget.style.borderColor = 'rgba(255,91,58,0.4)'; }}
            onBlur={(e) => { e.currentTarget.style.borderColor = 'var(--line)'; }}
          />
          <button
            onClick={() => send(input)}
            disabled={!input.trim() || loading}
            style={{
              padding: '10px 20px', flexShrink: 0,
              background: input.trim() && !loading ? 'rgba(255,91,58,0.12)' : 'transparent',
              border: `1px solid ${input.trim() && !loading ? 'rgba(255,91,58,0.4)' : 'var(--line)'}`,
              color: input.trim() && !loading ? 'var(--accent)' : 'var(--dim)',
              ...MONO, fontSize: '11px', letterSpacing: '1px',
              cursor: input.trim() && !loading ? 'pointer' : 'not-allowed',
              height: '60px',
            }}
          >
            {loading ? '…' : 'SEND →'}
          </button>
        </div>

        {/* Keyboard hint */}
        <div style={{
          flexShrink: 0, padding: '6px 20px 12px',
          ...MONO, fontSize: '9px', color: '#333', letterSpacing: '1px',
        }}>
          ↵ ENTER TO SEND · SHIFT+ENTER FOR NEWLINE · LAST 10 MESSAGES KEPT IN CONTEXT
        </div>
      </div>

      <style>{`
        @keyframes pulse {
          0%, 80%, 100% { opacity: 0.2; transform: scale(0.8); }
          40% { opacity: 1; transform: scale(1); }
        }
        .msg-row:hover .msg-actions { opacity: 1 !important; }
      `}</style>
    </div>
  );
}

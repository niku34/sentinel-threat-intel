'use client';
import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export default function LoginPage() {
  const router = useRouter();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;

    const chars = 'ஜயகவலமனட01010111000'.split('');
    const fontSize = 14;
    const columns = Math.floor(canvas.width / fontSize);
    const drops: number[] = Array(columns).fill(1);

    function draw() {
      ctx!.fillStyle = 'rgba(0,0,0,0.05)';
      ctx!.fillRect(0, 0, canvas!.width, canvas!.height);
      ctx!.fillStyle = 'rgba(255,91,58,0.35)';
      ctx!.font = `${fontSize}px monospace`;
      for (let i = 0; i < drops.length; i++) {
        const char = chars[Math.floor(Math.random() * chars.length)];
        ctx!.fillText(char, i * fontSize, drops[i] * fontSize);
        if (drops[i] * fontSize > canvas!.height && Math.random() > 0.975) drops[i] = 0;
        drops[i]++;
      }
    }

    const interval = setInterval(draw, 50);
    return () => clearInterval(interval);
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) { setError('Invalid credentials'); return; }
      const data = await res.json();
      localStorage.setItem('sentinel_token', data.access_token);
      router.push('/overview');
    } catch {
      setError('Connection error');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ position: 'fixed', inset: 0, background: '#000', overflow: 'hidden' }}>
      <canvas ref={canvasRef} style={{ position: 'absolute', inset: 0 }} />
      <div style={{
        position: 'absolute', inset: 0,
        background: 'radial-gradient(ellipse at 60% 50%, rgba(255,91,58,0.08) 0%, transparent 60%)',
        pointerEvents: 'none'
      }} />
      <div style={{
        position: 'absolute', inset: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'center'
      }}>
        <div style={{
          width: '400px',
          background: 'rgba(5,5,5,0.92)',
          border: '1px solid rgba(255,91,58,0.3)',
          padding: '48px',
          backdropFilter: 'blur(20px)'
        }}>
          <div style={{ textAlign: 'center', marginBottom: '32px' }}>
            <div style={{
              fontFamily: 'var(--font-bebas-neue), Bebas Neue, sans-serif',
              fontSize: '48px', letterSpacing: '4px', lineHeight: 1,
              color: '#f5f5f5'
            }}>
              BROADCASTER <span style={{ color: '#ff5b3a' }}>SHIELD</span>
            </div>
            <div style={{
              fontFamily: 'var(--font-space-mono), Space Mono, monospace',
              fontSize: '10px', color: '#444', letterSpacing: '3px',
              marginTop: '8px', textTransform: 'uppercase'
            }}>v1.0 · Console</div>
            <div style={{
              width: '40px', height: '1px', background: '#ff5b3a',
              margin: '16px auto 0'
            }} />
          </div>

          <form onSubmit={handleSubmit}>
            <div style={{ marginBottom: '16px' }}>
              <label style={{
                display: 'block',
                fontFamily: 'var(--font-space-mono), Space Mono, monospace',
                fontSize: '9px', color: '#555', letterSpacing: '2px',
                textTransform: 'uppercase', marginBottom: '8px'
              }}>Email</label>
              <input
                type="email" value={email}
                onChange={e => setEmail(e.target.value)}
                required
                style={{
                  width: '100%', background: '#050505',
                  border: '1px solid #1f1f1f', color: '#f5f5f5',
                  padding: '11px 14px', fontFamily: 'monospace',
                  fontSize: '13px', outline: 'none', boxSizing: 'border-box'
                }}
              />
            </div>
            <div style={{ marginBottom: '24px' }}>
              <label style={{
                display: 'block',
                fontFamily: 'var(--font-space-mono), Space Mono, monospace',
                fontSize: '9px', color: '#555', letterSpacing: '2px',
                textTransform: 'uppercase', marginBottom: '8px'
              }}>Password</label>
              <input
                type="password" value={password}
                onChange={e => setPassword(e.target.value)}
                required
                style={{
                  width: '100%', background: '#050505',
                  border: '1px solid #1f1f1f', color: '#f5f5f5',
                  padding: '11px 14px', fontFamily: 'monospace',
                  fontSize: '13px', outline: 'none', boxSizing: 'border-box'
                }}
              />
            </div>
            {error && (
              <div style={{
                color: '#ff3838', fontFamily: 'monospace',
                fontSize: '11px', marginBottom: '16px',
                border: '1px solid rgba(255,56,56,0.3)',
                padding: '8px 12px'
              }}>{error}</div>
            )}
            <button
              type="submit" disabled={loading}
              style={{
                width: '100%', background: loading ? '#b84028' : '#ff5b3a',
                color: 'white', border: 'none', padding: '13px',
                fontFamily: 'var(--font-bebas-neue), Bebas Neue, sans-serif',
                fontSize: '16px', letterSpacing: '3px', cursor: 'pointer',
                transition: 'background 0.15s'
              }}
            >{loading ? 'SIGNING IN...' : 'SIGN IN'}</button>
          </form>

          <div style={{
            textAlign: 'center', marginTop: '32px',
            fontFamily: 'var(--font-space-mono), Space Mono, monospace',
            fontSize: '9px', color: '#222', letterSpacing: '2px',
            textTransform: 'uppercase'
          }}>
            <span style={{ color: '#ff5b3a', marginRight: '8px' }}>●</span>
            Example Broadcaster · Internal Use Only
          </div>
        </div>
      </div>
    </div>
  );
}

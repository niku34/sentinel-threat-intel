import type { Metadata } from 'next';
import localFont from 'next/font/local';
import { ConditionalShell } from '@/components/conditional-shell';
import './globals.css';

const spaceGrotesk = localFont({
  src: [
    { path: '../../public/fonts/SpaceGrotesk-300.woff2', weight: '300', style: 'normal' },
    { path: '../../public/fonts/SpaceGrotesk-400.woff2', weight: '400', style: 'normal' },
    { path: '../../public/fonts/SpaceGrotesk-500.woff2', weight: '500', style: 'normal' },
    { path: '../../public/fonts/SpaceGrotesk-600.woff2', weight: '600', style: 'normal' },
    { path: '../../public/fonts/SpaceGrotesk-700.woff2', weight: '700', style: 'normal' },
  ],
  variable: '--font-space-grotesk',
  display: 'swap',
});

const spaceMono = localFont({
  src: [
    { path: '../../public/fonts/SpaceMono-400.woff2', weight: '400', style: 'normal' },
    { path: '../../public/fonts/SpaceMono-700.woff2', weight: '700', style: 'normal' },
  ],
  variable: '--font-space-mono',
  display: 'swap',
});

const bebasNeue = localFont({
  src: [
    { path: '../../public/fonts/BebasNeue-400.woff2', weight: '400', style: 'normal' },
  ],
  variable: '--font-bebas-neue',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'Broadcaster Shield — Unified AI Security Platform',
  description: 'AI-powered threat intelligence platform for Example Broadcaster',
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body
        className={`${spaceGrotesk.variable} ${spaceMono.variable} ${bebasNeue.variable}`}
      >
        <ConditionalShell>{children}</ConditionalShell>
      </body>
    </html>
  );
}

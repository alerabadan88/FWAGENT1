import './global.css';
import { RootProvider } from 'fumadocs-ui/provider';
import { createMetadata } from '@/lib/metadata';
import type { ReactNode } from 'react';

export const metadata = createMetadata();

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <RootProvider>{children}</RootProvider>
      </body>
    </html>
  );
}

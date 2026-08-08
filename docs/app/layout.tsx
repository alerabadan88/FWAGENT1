import './global.css';
import { RootProvider } from 'fumadocs-ui/provider';
import { createMetadata } from '@/lib/metadata';
import type { ReactNode } from 'react';

export const metadata = createMetadata();

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        {/* Static export ships the search index as a file rather than an
            endpoint, so the client has to be told to fetch and search it
            locally instead of querying a server that is not there. */}
        <RootProvider search={{ options: { type: 'static' } }}>
          {children}
        </RootProvider>
      </body>
    </html>
  );
}

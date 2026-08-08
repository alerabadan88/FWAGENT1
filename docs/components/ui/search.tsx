import { RootProvider } from 'fumadocs-ui/provider';

/**
 * Wraps fumadocs-ui's built-in search dialog, wired to /api/search.
 * fumadocs-ui/provider already renders the search trigger inside DocsLayout;
 * this wrapper exists as the customization point named in the project tree.
 */
export function SearchProvider({ children }: { children: React.ReactNode }) {
  return <RootProvider>{children}</RootProvider>;
}

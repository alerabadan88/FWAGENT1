import { createSearchAPI } from 'fumadocs-core/search/server';
import { source } from '@/lib/source';

// Static export has no server to answer a request, so the index is emitted as
// a file at build time and searched client-side.
export const dynamic = 'force-static';
export const revalidate = false;

export const { staticGET: GET } = createSearchAPI('advanced', {
  indexes: source.getPages().map((page) => ({
    title: page.data.title,
    description: page.data.description,
    structuredData: page.data.structuredData,
    id: page.url,
    url: page.url,
  })),
});

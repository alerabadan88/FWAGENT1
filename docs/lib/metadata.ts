import type { Metadata } from 'next';

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? 'http://localhost:3000';

export function createMetadata(override: Metadata = {}): Metadata {
  return {
    ...override,
    title: override.title ?? 'fw-automation-agent Docs',
    description:
      override.description ??
      'Documentation for the firmware automation agent pipeline: discovery, design, codegen, test, and deploy.',
    metadataBase: new URL(siteUrl),
    openGraph: {
      title: override.title ?? undefined,
      description: override.description ?? undefined,
      url: siteUrl,
      siteName: 'fw-automation-agent Docs',
      ...override.openGraph,
    },
  };
}

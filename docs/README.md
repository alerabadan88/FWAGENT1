# fw-automation-agent docs

Documentation site for fw-automation-agent, built with [Fumadocs](https://fumadocs.vercel.app/) on Next.js.

## Development

```bash
npm install
npm run dev
```

Open http://localhost:3000.

## Structure

- `app/` — Next.js App Router pages (home, docs layout, dynamic doc pages, search API route)
- `content/docs/` — documentation source in MDX: `getting-started`, `concepts`, `inputs`, `firmware`, `reference`, and `limitations`
- `components/ui/` — navigation/search/sidebar building blocks
- `components/custom/` — MDX-usable components (`CodeExample`, `ArchitectureDiagram`, `ApiExplorer`, `ComparisonTable`)
- `lib/source.ts` — Fumadocs source loader config
- `lib/metadata.ts` — shared page metadata helper

## Deployment

Built as a static export and published to GitHub Pages by
`.github/workflows/docs.yml` on any change under `docs/`.

The `GITHUB_PAGES=true` environment variable switches on the `/FWAGENT1`
basePath, which Pages needs because it serves a project site from a
subdirectory. Local `npm run dev` and `npm run build` keep a bare path.

Search is a build-time index rather than an endpoint, since a static export has
no server to answer one.

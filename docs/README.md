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
- `content/docs/` — documentation source in MDX, organized by section (`getting-started`, `architecture`, `core-components`, `agents`, `codegen`, `api`, `deployment`, `advanced`)
- `content/api-reference/openapi.json` — OpenAPI spec for the REST API
- `components/ui/` — navigation/search/sidebar building blocks
- `components/custom/` — MDX-usable components (`CodeExample`, `ArchitectureDiagram`, `ApiExplorer`, `ComparisonTable`)
- `lib/source.ts` — Fumadocs source loader config
- `lib/metadata.ts` — shared page metadata helper

## Status

Scaffold only — every page under `content/docs/` is a stub. Fill in real content as the `core/`, `codegen/`, and `agents/` implementations land.

import type { PageTree } from 'fumadocs-core/server';

interface SidebarProps {
  tree: PageTree.Root;
}

/**
 * Placeholder for a custom sidebar. DocsLayout (fumadocs-ui) renders its own
 * sidebar from `tree` by default; swap it in app/docs/layout.tsx if this
 * custom version is needed instead.
 */
export function Sidebar({ tree }: SidebarProps) {
  return (
    <aside className="w-64 shrink-0 border-r p-4 text-sm">
      {tree.children.map((item) =>
        item.type === 'page' ? (
          <div key={item.url} className="py-1">
            {item.name}
          </div>
        ) : null,
      )}
    </aside>
  );
}

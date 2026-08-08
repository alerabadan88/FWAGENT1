import Link from 'next/link';

const links = [
  { href: '/docs', label: 'Docs' },
  { href: '/docs/api', label: 'API' },
  { href: '/docs/architecture', label: 'Architecture' },
];

export function Navigation() {
  return (
    <nav className="flex items-center gap-4 text-sm">
      {links.map((link) => (
        <Link key={link.href} href={link.href} className="hover:text-fd-primary">
          {link.label}
        </Link>
      ))}
    </nav>
  );
}

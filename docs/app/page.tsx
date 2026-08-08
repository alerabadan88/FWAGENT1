import Link from 'next/link';

export default function HomePage() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-4 py-24 text-center">
      <h1 className="text-4xl font-bold">fw-automation-agent</h1>
      <p className="max-w-xl text-fd-muted-foreground">
        Documentation for the firmware automation agent pipeline — discovery,
        design, codegen, test, and deploy.
      </p>
      <Link
        href="/docs"
        className="rounded-lg bg-fd-primary px-4 py-2 text-sm font-medium text-fd-primary-foreground"
      >
        Read the docs
      </Link>
    </main>
  );
}

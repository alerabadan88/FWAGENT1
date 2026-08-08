interface CodeExampleProps {
  title?: string;
  children: React.ReactNode;
}

export function CodeExample({ title, children }: CodeExampleProps) {
  return (
    <div className="my-4 rounded-lg border">
      {title ? (
        <div className="border-b bg-fd-muted px-4 py-2 text-sm font-medium">
          {title}
        </div>
      ) : null}
      <div className="p-4">{children}</div>
    </div>
  );
}

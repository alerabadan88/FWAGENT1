interface ApiExplorerProps {
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
  path: string;
  description?: string;
}

const methodColor: Record<ApiExplorerProps['method'], string> = {
  GET: 'text-blue-500',
  POST: 'text-green-500',
  PUT: 'text-amber-500',
  DELETE: 'text-red-500',
  PATCH: 'text-purple-500',
};

export function ApiExplorer({ method, path, description }: ApiExplorerProps) {
  return (
    <div className="my-4 rounded-lg border p-4">
      <div className="flex items-center gap-2 font-mono text-sm">
        <span className={`font-bold ${methodColor[method]}`}>{method}</span>
        <span>{path}</span>
      </div>
      {description ? (
        <p className="mt-2 text-sm text-fd-muted-foreground">{description}</p>
      ) : null}
    </div>
  );
}

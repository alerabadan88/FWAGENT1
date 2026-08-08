interface ComparisonTableProps {
  headers: string[];
  rows: (string | number | boolean)[][];
}

export function ComparisonTable({ headers, rows }: ComparisonTableProps) {
  return (
    <div className="my-4 overflow-x-auto rounded-lg border">
      <table className="w-full text-sm">
        <thead className="bg-fd-muted">
          <tr>
            {headers.map((header) => (
              <th key={header} className="px-4 py-2 text-left font-medium">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-t">
              {row.map((cell, j) => (
                <td key={j} className="px-4 py-2">
                  {String(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

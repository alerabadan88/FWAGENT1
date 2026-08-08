interface ArchitectureDiagramProps {
  src: string;
  alt: string;
  caption?: string;
}

export function ArchitectureDiagram({ src, alt, caption }: ArchitectureDiagramProps) {
  return (
    <figure className="my-6">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt={alt} className="rounded-lg border" />
      {caption ? (
        <figcaption className="mt-2 text-center text-sm text-fd-muted-foreground">
          {caption}
        </figcaption>
      ) : null}
    </figure>
  );
}

import defaultMdxComponents from 'fumadocs-ui/mdx';
import type { MDXComponents } from 'mdx/types';
import { CodeExample } from '@/components/custom/code-example';
import { ArchitectureDiagram } from '@/components/custom/architecture-diagram';
import { ApiExplorer } from '@/components/custom/api-explorer';
import { ComparisonTable } from '@/components/custom/comparison-table';

export function getMDXComponents(components?: MDXComponents): MDXComponents {
  return {
    ...defaultMdxComponents,
    CodeExample,
    ArchitectureDiagram,
    ApiExplorer,
    ComparisonTable,
    ...components,
  };
}

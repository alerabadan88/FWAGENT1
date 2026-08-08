const { createMDX } = require('fumadocs-mdx/next');

const withMDX = createMDX();

// GitHub Pages serves the repo at /<repo>, and a static export cannot run
// Next's image optimiser or server routes.
const isPages = process.env.GITHUB_PAGES === 'true';
const basePath = isPages ? '/FWAGENT1' : '';

/** @type {import('next').NextConfig} */
const config = {
  reactStrictMode: true,
  output: 'export',
  basePath,
  // Pages has no trailing-slash rewrite, so emit directory-style URLs.
  trailingSlash: true,
  images: { unoptimized: true },
  env: { NEXT_PUBLIC_BASE_PATH: basePath },
};

module.exports = withMDX(config);

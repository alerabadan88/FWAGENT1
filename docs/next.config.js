const { createMDX } = require('fumadocs-mdx/next');

const withMDX = createMDX();

/** @type {import('next').NextConfig} */
const config = {
  reactStrictMode: true,
};

module.exports = withMDX(config);

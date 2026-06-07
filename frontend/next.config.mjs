/** @type {import('next').NextConfig} */
const nextConfig = {
  webpack: (config) => {
    // pdfjs-dist (via react-pdf) optionally references the Node `canvas` package
    // and ships as ESM. Without these, Next.js' webpack mis-handles pdf.mjs
    // ("Object.defineProperty called on non-object" at module eval).
    config.resolve.alias = {
      ...config.resolve.alias,
      canvas: false,
    };
    // pdfjs ships ESM .mjs; let webpack auto-detect the module type and not require
    // fully-specified imports, which fixes the harmony-interop crash at eval
    // ("Object.defineProperty called on non-object").
    config.module.rules.push({
      test: /\.m?js$/,
      resolve: { fullySpecified: false },
    });
    return config;
  },
};

export default nextConfig;

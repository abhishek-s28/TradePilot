/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    const backend = process.env.BACKEND_URL || "http://localhost:8000";
    return [
      // /api/v1/... → backend /api/v1/... (keep full path intact)
      { source: "/api/v1/:path*", destination: `${backend}/api/v1/:path*` },
      // /api/... → backend /... (strip the /api prefix for un-prefixed routes)
      { source: "/api/:path*",    destination: `${backend}/:path*` },
    ];
  },
};
module.exports = nextConfig;

/** @type {import('next').NextConfig} */
const api = process.env.API_INTERNAL_URL || "http://localhost:8000";

module.exports = {
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
  async headers() {
    // No framing: another page must not overlay the Approve button in an invisible iframe.
    return [{
      source: "/:path*",
      headers: [
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
      ],
    }];
  },
};

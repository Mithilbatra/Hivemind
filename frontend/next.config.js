/** @type {import('next').NextConfig} */
const api = process.env.API_INTERNAL_URL || "http://localhost:8000";

module.exports = {
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
};

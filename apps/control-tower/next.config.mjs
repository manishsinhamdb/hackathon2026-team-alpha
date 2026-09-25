/** @type {import('next').NextConfig} */
const nextConfig = {
  // Emit a self-contained server bundle (.next/standalone) so the Docker image is a single
  // small container with no need to ship node_modules or run `next start`. Host-agnostic:
  // the standalone server.js honours PORT and HOSTNAME, which is all Kanopy needs.
  output: "standalone",
  reactStrictMode: true,
  // No eslint config ships with this app; type-checking still runs during build.
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;

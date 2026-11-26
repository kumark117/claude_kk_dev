import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Keep Next.js inside frontend/, whatever lockfiles exist further up.
  turbopack: { root: __dirname },
  // Do not let `next dev` write agent instruction files into the project.
  agentRules: false,
};

export default nextConfig;

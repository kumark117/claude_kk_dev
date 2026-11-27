import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Stop "next dev" from writing an AGENTS.md file.
  agentRules: false,
  // Keep Turbopack from picking a lockfile outside this folder as the root.
  turbopack: { root: __dirname },
};

export default nextConfig;

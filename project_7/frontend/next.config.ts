import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Keep Turbopack inside frontend/ even if a lockfile exists higher up.
  turbopack: { root: __dirname },
  // Stop `next dev` from writing AGENTS.md into the project.
  agentRules: false,
};

export default nextConfig;

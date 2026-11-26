import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Keep Turbopack inside frontend/ even if a lock file exists further up.
  turbopack: { root: __dirname },
  // Stop `next dev` from writing AGENTS.md and CLAUDE.md files.
  agentRules: false,
};

export default nextConfig;

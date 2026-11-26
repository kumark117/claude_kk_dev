import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Keep Turbopack inside frontend/, whatever lies in the parent directories.
  turbopack: { root: __dirname },
  // Stop "next dev" from writing an AGENTS.md file into the project.
  agentRules: false,
};

export default nextConfig;

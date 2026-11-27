import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Without this, a lockfile higher up could be mistaken for the project root.
  turbopack: { root: __dirname },
  // Stops `next dev` from writing an AGENTS.md file into the project.
  agentRules: false,
};

export default nextConfig;

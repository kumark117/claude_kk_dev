import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Keep Turbopack inside this folder, whatever lockfiles exist further up.
  turbopack: { root: __dirname },
  // Stop `next dev` from writing AGENTS.md into the project.
  agentRules: false,
};

export default nextConfig;

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Stop `next dev` from writing AGENTS.md files into the project.
  agentRules: false,
  // This folder is the application root, whatever lockfiles exist above it.
  turbopack: { root: __dirname },
};

export default nextConfig;

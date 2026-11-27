import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Keep Next.js from picking a parent folder as the workspace root.
  turbopack: { root: __dirname },
  // Stop `next dev` writing AGENTS.md into the project.
  agentRules: false,
};

export default nextConfig;

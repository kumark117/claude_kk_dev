import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Do not let `next dev` write AGENTS.md or CLAUDE.md into the project.
  agentRules: false,
  // Keep Turbopack inside frontend/, whatever lockfiles exist further up.
  turbopack: { root: __dirname },
};

export default nextConfig;

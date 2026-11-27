import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Stop `next dev` from writing an AGENTS.md file into the project.
  agentRules: false,
  // Without this, a lockfile higher up the tree can be mistaken for the root.
  turbopack: { root: path.resolve(__dirname) },
};

export default nextConfig;

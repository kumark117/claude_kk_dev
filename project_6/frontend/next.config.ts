import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Keep Turbopack inside this folder even if a lockfile exists higher up.
  turbopack: { root: path.resolve(__dirname) },
  // Stop "next dev" from writing AGENTS.md into this folder.
  agentRules: false,
};

export default nextConfig;

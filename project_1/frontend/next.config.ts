import path from "node:path";
import { fileURLToPath } from "node:url";
import type { NextConfig } from "next";

const here = path.dirname(fileURLToPath(import.meta.url));

const nextConfig: NextConfig = {
  // Without this, Next.js may pick a parent folder that holds another lockfile.
  turbopack: { root: here },
  // Stops `next dev` from writing an AGENTS.md file into this folder.
  agentRules: false,
};

export default nextConfig;

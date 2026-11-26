import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Keep Turbopack inside frontend/ even if lockfiles exist higher up.
  turbopack: { root: __dirname },
};

export default nextConfig;

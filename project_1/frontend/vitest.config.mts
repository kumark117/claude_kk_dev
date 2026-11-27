import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    // Component tests opt in to jsdom with a per-file comment.
    environment: "node",
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});

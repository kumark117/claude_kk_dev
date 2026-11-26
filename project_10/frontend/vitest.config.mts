import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    include: ["tests/**/*.test.{ts,tsx}"],
    // Component tests opt into jsdom with a per-file comment.
    environment: "node",
  },
});

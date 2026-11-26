import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    // AskForm.test.tsx switches to jsdom with a file comment.
    environment: "node",
    include: ["tests/**/*.test.{ts,tsx}"],
    testTimeout: 30_000,
  },
});

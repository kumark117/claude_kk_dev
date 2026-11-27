import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    // Each test file picks its environment with a "@vitest-environment" comment.
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});

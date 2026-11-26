import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    // Component tests switch to jsdom with a "@vitest-environment" comment.
    environment: "node",
    include: ["tests/**/*.test.{ts,tsx}"],
    // Generous limits: a cold jsdom on a busy machine can take many seconds.
    testTimeout: 30_000,
  },
});

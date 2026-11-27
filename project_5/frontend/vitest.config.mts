import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    // Component tests opt into jsdom with a "@vitest-environment" comment.
    environment: "node",
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});

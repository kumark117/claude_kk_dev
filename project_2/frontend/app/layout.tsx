import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = { title: "Ask the docs" };

const styles = `
  body { font-family: system-ui, sans-serif; line-height: 1.5; margin: 0; }
  main { max-width: 40rem; margin: 2rem auto; padding: 0 1rem; }
  .row { display: flex; gap: 0.5rem; }
  input { flex: 1; font: inherit; padding: 0.4rem; }
  button { font: inherit; padding: 0.4rem 1rem; }
  .text { white-space: pre-wrap; overflow-wrap: anywhere; }
  .error { color: #b00020; }
`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <style>{styles}</style>
      </head>
      <body>{children}</body>
    </html>
  );
}

import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Ask the docs",
};

const styles = `
  body { font-family: system-ui, sans-serif; line-height: 1.5; margin: 0; }
  main { max-width: 40rem; margin: 0 auto; padding: 1.5rem; }
  label { display: block; margin-bottom: 0.25rem; }
  .row { display: flex; gap: 0.5rem; }
  input { flex: 1; min-width: 0; font: inherit; padding: 0.4rem 0.6rem; }
  button { font: inherit; padding: 0.4rem 1rem; }
  ul { list-style: none; padding: 0; }
  li { border: 1px solid #ccc; border-radius: 4px; padding: 0 0.75rem; margin-bottom: 0.75rem; }
  .text { white-space: pre-wrap; overflow-wrap: anywhere; }
  .score { color: #666; font-size: 0.875rem; }
  .error { color: #b00020; }
`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <style>{styles}</style>
        {children}
      </body>
    </html>
  );
}

import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = { title: "Ask the docs" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body
        style={{
          fontFamily: "system-ui, sans-serif",
          maxWidth: "40rem",
          margin: "2rem auto",
          padding: "0 1rem",
        }}
      >
        <main>{children}</main>
      </body>
    </html>
  );
}

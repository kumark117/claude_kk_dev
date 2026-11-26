import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Ask the docs",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body
        style={{
          fontFamily: "system-ui, sans-serif",
          lineHeight: 1.5,
          maxWidth: "40rem",
          margin: "2rem auto",
          padding: "0 1rem",
          overflowWrap: "anywhere",
        }}
      >
        {children}
      </body>
    </html>
  );
}

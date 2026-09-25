import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "POC Builder — Control Tower",
  description: "Chat with the POC Builder and watch a POC's pipeline live.",
};

// Light is the default theme; the app also honours prefers-color-scheme: dark.
export const viewport = {
  colorScheme: "light dark",
  themeColor: "#001E2B",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

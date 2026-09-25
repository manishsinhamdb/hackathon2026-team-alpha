import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "POC Builder — Control Tower",
  description: "Chat with the POC Builder and watch a POC's pipeline live.",
};

// Single dark theme in this round (the approved design). Fonts come from Google Fonts via <link> so the
// container build has no font-fetch step; the browser loads them and falls back to system fonts if blocked.
export const viewport = {
  colorScheme: "dark",
  themeColor: "#001E2B",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600;700&family=Sora:wght@600;700&display=swap"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}

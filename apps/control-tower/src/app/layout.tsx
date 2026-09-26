import type { Metadata } from "next";
import "./globals.css";
import { THEME_INIT_SCRIPT } from "@/lib/theme";
import { SPLIT_INIT_SCRIPT } from "@/lib/split";

export const metadata: Metadata = {
  title: "POC Builder — Control Tower",
  description: "Chat with the POC Builder and watch a POC's pipeline live.",
};

// Light + dark themes (data-theme on <html>, palette in globals.css). `data-theme="dark"` is the SSR
// fallback; the inline script below corrects it to the persisted/OS choice before first paint (no flash).
// Both `light dark` are advertised so form controls/scrollbars adapt. Fonts come from Google Fonts via
// <link> so the container build has no font-fetch step; falls back to system fonts if blocked.
export const viewport = {
  colorScheme: "light dark",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // suppressHydrationWarning: the pre-paint script below flips data-theme before React hydrates.
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        {/* Apply the persisted/OS theme before paint to avoid a flash. Mirrors src/lib/theme.ts. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
        {/* Apply the persisted split width before paint (no layout flash). Mirrors src/lib/split.ts. */}
        <script dangerouslySetInnerHTML={{ __html: SPLIT_INIT_SCRIPT }} />
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

import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Control Tower — POC Builder",
  description: "Chat with the POC Builder and watch a POC's pipeline live.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

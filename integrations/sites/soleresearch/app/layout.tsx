import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sole Research",
  description: "A public, read-only view of local-first evidence research.",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

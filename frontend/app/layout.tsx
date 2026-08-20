import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

import { Providers } from "./providers";
import { Toaster } from "@/components/ui/sonner";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Agent Hub",
  description: "Connect your services. Build an agent. Ask it anything.",
};

/**
 * The root layout.
 *
 * This is a SERVER component - note there is no "use client" here. It
 * renders once on the server and streams HTML, which is why the
 * interactive parts are pushed into <Providers>, which is a client
 * component.
 *
 * Keeping the root on the server matters: everything a client
 * component imports ships to the browser, so marking this file "use
 * client" would drag the entire app into the JavaScript bundle.
 */
export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <Providers>{children}</Providers>
        <Toaster richColors position="top-right" />
      </body>
    </html>
  );
}

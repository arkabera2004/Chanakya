import type { Metadata } from "next";
import { Geist, Michroma } from "next/font/google";
import { cookies } from "next/headers";
import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/shell";
import { DeskProvider } from "@/context/desk";
import { ThemeProvider } from "@/context/theme-context";
import { THEME_STORAGE_KEY, type ThemeMode } from "@/lib/theme";

import "./globals.css";

const geist = Geist({
  variable: "--font-geist",
  subsets: ["latin"],
  display: "swap",
});

const michroma = Michroma({
  variable: "--font-michroma",
  subsets: ["latin"],
  weight: "400",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Chanakya discovery desk",
  description: "Vira watches government procurement and walks Wayam through every lead she keeps.",
  icons: { icon: "/icon.png", apple: "/brand/mark-512.png" },
  openGraph: {
    title: "Chanakya discovery desk",
    description: "Vira watches government procurement and walks Wayam through every lead she keeps.",
    images: [{ url: "/brand/og-card.png", width: 1200, height: 630 }],
  },
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  const cookieStore = await cookies();
  const stored = cookieStore.get(THEME_STORAGE_KEY)?.value;
  const theme: ThemeMode = stored === "light" ? "light" : "dark";

  return (
    <html lang="en" data-theme={theme} style={{ colorScheme: theme }} className={`${geist.variable} ${michroma.variable} h-full antialiased`} suppressHydrationWarning>
      <body className="h-full overflow-hidden">
        <ThemeProvider initialTheme={theme}>
          <DeskProvider>
            <AppShell>{children}</AppShell>
          </DeskProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}

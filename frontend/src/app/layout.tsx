import "@/styles/globals.css";
import type { Metadata, Viewport } from "next";
import { QueryProvider } from "@/lib/QueryProvider";
import { AppShell } from "@/components/AppShell";

export const metadata: Metadata = {
  title: "Tradebot",
  description: "Trading bot platform — stocks & options",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  themeColor: "#0b0d10",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body>
        <QueryProvider>
          <AppShell>{children}</AppShell>
        </QueryProvider>
      </body>
    </html>
  );
}

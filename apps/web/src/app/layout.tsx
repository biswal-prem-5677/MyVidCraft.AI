import "./globals.css";
import type { ReactNode } from "react";

export const metadata = {
  title: "MyVidCraft.ai (Phase 1)",
  description: "Autonomous Content Brain → multi-agent script (Phase 1 vertical slice)"
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}


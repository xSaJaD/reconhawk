import "./globals.css";
import type { ReactNode } from "react";

export const metadata = {
  title: "ReconHawk",
  description: "Autonomous web security research console",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RoleMatch",
  description: "Resume-based job discovery and application tracking with evidence you can check.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "TactLink — Position without sight",
  description:
    "Camera-free, GPS-independent relative positioning. Explore TactLink’s UWB ranging geometry, five-peer scheduler, and on-device gesture architecture.",
};
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

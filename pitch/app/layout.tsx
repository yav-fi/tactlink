import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = {
  metadataBase: new URL('https://dnhacks26.arulandu.com'),
  alternates: { canonical: '/' },
  title: 'TactLink — Add the drone to the squad',
  description:
    'A vision for a drone that works alongside the squad. Explore mission planning, gesture-based interaction, and shared spatial awareness through our connected simulation prototype.',
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

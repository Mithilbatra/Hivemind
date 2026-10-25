import "./globals.css";
import Shell from "@/components/Shell";

export const metadata = {
  title: "Hivemind",
  description: "Many coding agents, one shared mind: plan, run, validate and review AI coding work.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body><Shell>{children}</Shell></body>
    </html>
  );
}

import "./globals.css";
import Shell from "@/components/Shell";

export const metadata = { title: "Code Orchestrator", description: "Plan, run, validate and review AI coding tasks" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body><Shell>{children}</Shell></body>
    </html>
  );
}

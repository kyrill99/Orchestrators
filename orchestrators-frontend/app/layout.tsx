import type { Metadata } from "next";
import { Suspense } from "react";
import { Inter, Geist_Mono } from "next/font/google";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AgentDrawerProvider } from "@/components/layout/agent-drawer-context";
import { SideNav } from "@/components/layout/side-nav";
import { TopBar } from "@/components/layout/top-bar";
import { AgentFab } from "@/components/layout/agent-fab";
import { AgentDrawer } from "@/components/agent/agent-drawer";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Orchestrators — Chunking evaluation",
  description: "Linear-inspired orchestration UI mock",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full font-sans">
        <TooltipProvider>
          <AgentDrawerProvider>
            <div className="flex h-svh overflow-hidden bg-shell text-foreground">
              <Suspense
                fallback={<aside className="w-[220px] shrink-0 bg-shell" />}
              >
                <SideNav />
              </Suspense>

              <div className="flex min-w-0 flex-1 flex-col p-2 pl-0">
                <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[12px] border border-black/5 bg-background shadow-[0_0_0_1px_rgba(0,0,0,0.02),0_2px_8px_rgba(0,0,0,0.04)]">
                  <Suspense
                    fallback={
                      <header className="h-11 shrink-0 border-b border-border" />
                    }
                  >
                    <TopBar />
                  </Suspense>
                  <main className="min-h-0 flex-1 overflow-hidden">
                    {children}
                  </main>
                </div>
                <div className="mt-1.5 flex h-8 shrink-0 items-center justify-end pr-0.5">
                  <AgentFab />
                </div>
              </div>

              <AgentDrawer />
            </div>
          </AgentDrawerProvider>
        </TooltipProvider>
      </body>
    </html>
  );
}

"use client";

import { Send } from "lucide-react";
import { chatMessages } from "@/lib/fixtures";
import { useAgentDrawer } from "@/components/layout/agent-drawer-context";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

export function AgentDrawer() {
  const { open, setOpen } = useAgentDrawer();

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetContent
        side="right"
        className="flex w-full flex-col gap-0 p-0 sm:max-w-[400px]"
      >
        <SheetHeader className="gap-0.5 border-b border-border px-4 py-3 text-left">
          <SheetTitle className="text-[13px] font-medium">Agent</SheetTitle>
          <SheetDescription className="text-xs">
            Strategy interview · mock conversation
          </SheetDescription>
        </SheetHeader>

        <ScrollArea className="flex-1">
          <div className="flex flex-col gap-3 px-4 py-4">
            {chatMessages.map((message) => (
              <div
                key={message.id}
                className={cn(
                  "max-w-[92%] rounded-lg px-3 py-2 text-[13px] leading-relaxed",
                  message.role === "agent"
                    ? "self-start bg-accent text-foreground"
                    : "self-end bg-foreground text-background",
                )}
              >
                {message.content}
              </div>
            ))}
          </div>
        </ScrollArea>

        <div className="border-t border-border p-3">
          <div className="relative">
            <Textarea
              placeholder="Ask about goals, priorities, or success…"
              className="min-h-[76px] resize-none pr-10 text-[13px]"
              readOnly
              aria-label="Reply (disabled in UI mock)"
            />
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              className="absolute right-2 bottom-2"
              disabled
              aria-label="Send disabled"
            >
              <Send className="size-3.5" />
            </Button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}

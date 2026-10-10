"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

type AgentDrawerContextValue = {
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
};

const AgentDrawerContext = createContext<AgentDrawerContextValue | null>(null);

export function AgentDrawerProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const toggle = useCallback(() => setOpen((value) => !value), []);
  const value = useMemo(
    () => ({ open, setOpen, toggle }),
    [open, toggle],
  );

  return (
    <AgentDrawerContext.Provider value={value}>
      {children}
    </AgentDrawerContext.Provider>
  );
}

export function useAgentDrawer() {
  const context = useContext(AgentDrawerContext);
  if (!context) {
    throw new Error("useAgentDrawer must be used within AgentDrawerProvider");
  }
  return context;
}

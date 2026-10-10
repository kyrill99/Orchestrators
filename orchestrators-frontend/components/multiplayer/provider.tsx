"use client";

import {
  createContext,
  Suspense,
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  MultiplayerClient,
  type Snapshot,
  type Viewport,
} from "@/lib/multiplayer/client";
import {
  presenceColor,
  presenceName,
  setRoute,
  usePresenceOf,
} from "@/lib/presence";

const Context = createContext<MultiplayerClient | null>(null);

/**
 * The multiplayer layer on top of lib/presence.ts: one WebSocket per tab for
 * cursors, follow mode and live broker events. Mount once in the layout.
 */
export function MultiplayerProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState(() => new MultiplayerClient());

  useEffect(() => {
    client.start();
    return () => client.stop();
  }, [client]);

  return (
    <Context.Provider value={client}>
      {children}
      {/* Route hooks need Suspense so prerendering is not blocked. */}
      <Suspense fallback={null}>
        <RouteReporter />
        <FollowController />
      </Suspense>
    </Context.Provider>
  );
}

export function useMultiplayerClient(): MultiplayerClient | null {
  return useContext(Context);
}

const EMPTY: Snapshot = { mode: "connecting", following: null };
const noop = () => () => {};

export function useMultiplayer(): Snapshot {
  const client = useContext(Context);
  return useSyncExternalStore(
    client?.subscribe ?? noop,
    client?.getSnapshot ?? (() => EMPTY),
    () => EMPTY,
  );
}

/** Broker events (e.g. "graph") over the shared connection; "resync" on reconnect. */
export function useChannel(
  channel: string,
  listener: (type: string, data: unknown) => void,
) {
  const client = useContext(Context);
  const ref = useRef(listener);
  useEffect(() => {
    ref.current = listener;
  });
  useEffect(() => {
    if (!client) return;
    return client.on(channel, (type, data) => ref.current(type, data));
  }, [client, channel]);
}

/** Join the cursor room for a surface while it is mounted. */
export function useCursorRoom(location: string | null) {
  const client = useContext(Context);
  useEffect(() => {
    if (!client || !location) return;
    return client.join(location);
  }, [client, location]);
}

/** Apply the followed person's viewport (graph surfaces call this). */
export function useFollowViewport(location: string, apply: (v: Viewport) => void) {
  const client = useContext(Context);
  const ref = useRef(apply);
  useEffect(() => {
    ref.current = apply;
  });
  useEffect(() => {
    if (!client) return;
    return client.onViewport((v) => {
      if (v.location === location) ref.current(v);
    });
  }, [client, location]);
}

/** Shares this tab's route through presence, so others can follow it. */
function RouteReporter() {
  const pathname = usePathname();
  useEffect(() => {
    setRoute(pathname);
  }, [pathname]);
  return null;
}

/** Navigates along with the followed person; Esc or moving yourself stops. */
function FollowController() {
  const client = useContext(Context);
  const { following } = useMultiplayer();
  const target = usePresenceOf(following);
  const router = useRouter();
  const pathname = usePathname();

  const route = target?.route;
  useEffect(() => {
    if (route && route !== pathname) router.push(route);
  }, [route, pathname, router]);

  useEffect(() => {
    if (!following || !client) return;
    const stop = () => client.follow(null);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") stop();
    };
    // Registered after the click that started following.
    const id = window.setTimeout(() => {
      window.addEventListener("keydown", onKey);
      window.addEventListener("wheel", stop, { passive: true });
      window.addEventListener("pointerdown", stop);
    }, 0);
    return () => {
      window.clearTimeout(id);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("wheel", stop);
      window.removeEventListener("pointerdown", stop);
    };
  }, [following, client]);

  if (!following || !target) return null;
  const color = presenceColor(target.email);
  return (
    <div className="pointer-events-none fixed inset-0 z-[100]" aria-live="polite">
      <div
        className="absolute inset-0 rounded-[10px] border-2"
        style={{ borderColor: color }}
      />
      <div
        className="absolute top-2 left-1/2 -translate-x-1/2 rounded-full px-3 py-1 text-[12px] font-medium text-white shadow-sm"
        style={{ backgroundColor: color }}
      >
        Following {presenceName(target)} · Esc to stop
      </div>
    </div>
  );
}

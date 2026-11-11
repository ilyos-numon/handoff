import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { Api, ApiError } from "./api";
import type { DemoStatus, Me } from "./types";

interface AppState {
  ready: boolean;
  me: Me | null;
  demo: DemoStatus | null;
  error: ApiError | null;
  reload: () => Promise<void>;
}
const Ctx = createContext<AppState | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<Omit<AppState, "reload">>({ ready: false, me: null, demo: null, error: null });
  const reload = useCallback(async () => {
    const [me, demo] = await Promise.allSettled([Api.me(), Api.demoStatus()]);
    setState({
      ready: true,
      me: me.status === "fulfilled" ? me.value : null,
      demo: demo.status === "fulfilled" ? demo.value : null,
      error: me.status === "rejected" ? (me.reason as ApiError) : null,
    });
  }, []);
  useEffect(() => { void reload(); }, [reload]);
  return <Ctx.Provider value={{ ...state, reload }}>{children}</Ctx.Provider>;
}

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("AppProvider missing");
  return v;
}

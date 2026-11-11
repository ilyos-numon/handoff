import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

/** 비동기 동작 하나: 진행 중 잠금 + 오류 보관. 중복 클릭은 무시한다. */
export function useAction<A extends unknown[], R>(fn: (...a: A) => Promise<R>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const lock = useRef(false);
  const run = useCallback(async (...a: A): Promise<R | undefined> => {
    if (lock.current) return undefined;
    lock.current = true; setBusy(true); setError(null);
    try { return await fn(...a); }
    catch (e) { setError(e instanceof ApiError ? e : new ApiError(0, "network")); return undefined; }
    finally { lock.current = false; setBusy(false); }
  }, [fn]);
  return { run, busy, error, setError };
}

/** GET 하나: 처음 불러오기 + reload. 404 를 빈 상태로 다루고 싶으면 empty404. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[], opts: { empty404?: boolean } = {}) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const reload = useCallback(async () => {
    setLoading(true);
    try { setData(await fn()); setError(null); }
    catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(0, "network");
      if (opts.empty404 && err.status === 404) { setData(null); setError(null); }
      else setError(err);
    } finally { setLoading(false); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { void reload(); }, [reload]);
  return { data, setData, loading, error, reload };
}

/** 창이 다시 보일 때마다 불러온다(푸시 경로가 없다). */
export function useOnFocus(cb: () => void) {
  useEffect(() => {
    const h = () => { if (document.visibilityState === "visible") cb(); };
    document.addEventListener("visibilitychange", h);
    return () => document.removeEventListener("visibilitychange", h);
  }, [cb]);
}

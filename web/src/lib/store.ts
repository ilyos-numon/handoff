// localStorage 는 막힐 수 있다(사파리 개인 창 등) → 모두 try/catch.
export const store = {
  get(k: string): string | null { try { return localStorage.getItem(k); } catch { return null; } },
  set(k: string, v: string) { try { localStorage.setItem(k, v); } catch { /* */ } },
  del(k: string) { try { localStorage.removeItem(k); } catch { /* */ } },
  json<T>(k: string, fallback: T): T {
    const raw = store.get(k);
    if (!raw) return fallback;
    try { return JSON.parse(raw) as T; } catch { return fallback; }
  },
  setJson(k: string, v: unknown) { store.set(k, JSON.stringify(v)); },
};
export const keys = {
  session: (leaver: string) => `handoff.session.${leaver}`,
  transcript: (sid: string) => `handoff.transcript.${sid}`,
  drills: (leaver: string) => `handoff.drills.${leaver}`,
};

import { useEffect, useState } from "react";
import { store } from "./store";

export type ThemeChoice = "system" | "light" | "dark";
export const THEME_KEY = "handoff.theme";
export const THEME_LABEL: Record<ThemeChoice, string> = { system: "기기 설정", light: "밝게", dark: "어둡게" };
export const THEME_NEXT: Record<ThemeChoice, ThemeChoice> = { system: "light", light: "dark", dark: "system" };
export const THEME_EVENT = "handoff:theme";

function emit(c: ThemeChoice) {
  window.dispatchEvent(new CustomEvent<ThemeChoice>(THEME_EVENT, { detail: c }));
}

export function readTheme(): ThemeChoice {
  const v = store.get(THEME_KEY);
  return v === "light" || v === "dark" ? v : "system";
}

export function applyTheme(c: ThemeChoice): void {
  const root = document.documentElement;
  if (c === "light" || c === "dark") root.setAttribute("data-theme", c);
  else root.removeAttribute("data-theme");
}

export function saveTheme(c: ThemeChoice): void {
  applyTheme(c);
  if (c === "system") store.del(THEME_KEY);
  else store.set(THEME_KEY, c);
  emit(c);
}

export function watchTheme(): () => void {
  const onStorage = (e: StorageEvent) => {
    if (e.key !== null && e.key !== THEME_KEY) return;
    const c = readTheme();
    applyTheme(c);
    emit(c);
  };
  window.addEventListener("storage", onStorage);
  return () => window.removeEventListener("storage", onStorage);
}

export function useTheme(): [ThemeChoice, (c: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>(readTheme);
  useEffect(() => {
    const on = (e: Event) => setChoice((e as CustomEvent<ThemeChoice>).detail);
    window.addEventListener(THEME_EVENT, on);
    return () => window.removeEventListener(THEME_EVENT, on);
  }, []);
  return [choice, saveTheme];
}

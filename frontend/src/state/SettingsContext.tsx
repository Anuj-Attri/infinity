// App-wide settings: hour format, custom system prompt, theme.
// Persists to localStorage and re-renders consumers on change.
// Theme resolution: "light" | "dark" | "system" (follows OS prefers-color-scheme).
// Applies data-theme to document.documentElement for CSS variable switching.

import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
  type ReactNode,
} from "react";

export type HourFormat = "12" | "24";
export type Theme = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export type AppSettings = {
  hourFormat: HourFormat;
  customSystemPrompt: string;
  theme: Theme;
  accent: string;   // "default" | theme-specific ids (e.g. "obsidian", "sky")
};

type Ctx = {
  settings: AppSettings;
  resolvedTheme: ResolvedTheme;
  setHourFormat: (f: HourFormat) => void;
  setCustomSystemPrompt: (text: string) => void;
  resetCustomSystemPrompt: () => void;
  setTheme: (t: Theme) => void;
  accent: string;
  setAccent: (a: string) => void;
};

const KEY = "infinity.settings.v1";
const DEFAULTS: AppSettings = {
  hourFormat: "24",
  customSystemPrompt: "",
  theme: "system",
  accent: "default",
};

function load(): AppSettings {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULTS;
    const parsed = JSON.parse(raw);
    return {
      hourFormat: parsed?.hourFormat === "12" ? "12" : "24",
      customSystemPrompt: typeof parsed?.customSystemPrompt === "string" ? parsed.customSystemPrompt : "",
      theme: (["light", "dark", "system"] as Theme[]).includes(parsed?.theme) ? parsed.theme : "system",
      accent: typeof parsed?.accent === "string" ? parsed.accent : "default",
    };
  } catch { return DEFAULTS; }
}

function save(s: AppSettings) {
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* ignore */ }
}

function osTheme(): ResolvedTheme {
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

const SettingsCtx = createContext<Ctx | null>(null);

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<AppSettings>(() => load());
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>(() => {
    const t = load().theme;
    return t === "light" ? "light" : t === "dark" ? "dark" : osTheme();
  });

  // Sync resolvedTheme when settings.theme changes or OS preference changes.
  useEffect(() => {
    if (settings.theme === "system") {
      const mq = matchMedia("(prefers-color-scheme: dark)");
      const handler = (e: MediaQueryListEvent) => setResolvedTheme(e.matches ? "dark" : "light");
      setResolvedTheme(mq.matches ? "dark" : "light");
      mq.addEventListener("change", handler);
      return () => mq.removeEventListener("change", handler);
    } else {
      setResolvedTheme(settings.theme as ResolvedTheme);
    }
  }, [settings.theme]);

  // Apply to DOM.
  useEffect(() => {
    document.documentElement.dataset.theme = resolvedTheme;
  }, [resolvedTheme]);

  // Apply accent variant (data-accent) — CSS overrides key vars per theme.
  useEffect(() => {
    document.documentElement.dataset.accent = settings.accent || "default";
  }, [settings.accent]);

  useEffect(() => { save(settings); }, [settings]);

  const setHourFormat = useCallback((f: HourFormat) => {
    setSettings((prev) => ({ ...prev, hourFormat: f }));
  }, []);
  const setCustomSystemPrompt = useCallback((text: string) => {
    setSettings((prev) => ({ ...prev, customSystemPrompt: text }));
  }, []);
  const resetCustomSystemPrompt = useCallback(() => {
    setSettings((prev) => ({ ...prev, customSystemPrompt: "" }));
  }, []);
  const setTheme = useCallback((t: Theme) => {
    setSettings((prev) => ({ ...prev, theme: t }));
  }, []);
  const setAccent = useCallback((a: string) => {
    setSettings((prev) => ({ ...prev, accent: a }));
  }, []);

  const value = useMemo<Ctx>(() => ({
    settings, resolvedTheme,
    setHourFormat, setCustomSystemPrompt, resetCustomSystemPrompt, setTheme,
    accent: settings.accent, setAccent,
  }), [settings, resolvedTheme, setHourFormat, setCustomSystemPrompt, resetCustomSystemPrompt, setTheme, setAccent]);

  return <SettingsCtx.Provider value={value}>{children}</SettingsCtx.Provider>;
}

export function useSettings(): Ctx {
  const v = useContext(SettingsCtx);
  if (!v) throw new Error("useSettings outside SettingsProvider");
  return v;
}

"use client";

// Light/dark theme, driven by a `data-theme` attribute on <html>. The palette itself lives in CSS
// variables (globals.css); this module only decides which theme is active and persists the choice.
// Default follows the OS `prefers-color-scheme`; an explicit user choice is stored in localStorage and
// wins. A tiny inline script in layout.tsx applies the same logic before first paint to avoid a flash;
// keep the two in sync.
import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";
export const THEME_KEY = "ct.theme";

// Pure: resolve the theme from a stored value + the OS preference. Exported for tests.
export function resolveTheme(stored: string | null, prefersDark: boolean): Theme {
  if (stored === "light" || stored === "dark") return stored;
  return prefersDark ? "dark" : "light";
}

export function applyTheme(theme: Theme): void {
  if (typeof document !== "undefined") document.documentElement.setAttribute("data-theme", theme);
}

function currentTheme(): Theme {
  if (typeof document === "undefined") return "dark";
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr === "light" || attr === "dark") return attr;
  const stored = typeof localStorage !== "undefined" ? localStorage.getItem(THEME_KEY) : null;
  const prefersDark =
    typeof matchMedia !== "undefined" ? matchMedia("(prefers-color-scheme: dark)").matches : true;
  return resolveTheme(stored, prefersDark);
}

// The pre-paint script (inlined in layout.tsx head). Kept as a string so the markup stays identical.
export const THEME_INIT_SCRIPT = `(function(){try{var s=localStorage.getItem('${THEME_KEY}');var t=(s==='light'||s==='dark')?s:(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');document.documentElement.setAttribute('data-theme',t);}catch(e){document.documentElement.setAttribute('data-theme','dark');}})();`;

export function useTheme(): { theme: Theme; toggle: () => void; setTheme: (t: Theme) => void } {
  const [theme, setThemeState] = useState<Theme>("dark");

  useEffect(() => {
    setThemeState(currentTheme());
  }, []);

  const setTheme = useCallback((t: Theme) => {
    applyTheme(t);
    try {
      localStorage.setItem(THEME_KEY, t);
    } catch {
      /* storage unavailable — the in-memory + attribute state still applies for this session */
    }
    setThemeState(t);
  }, []);

  const toggle = useCallback(() => setTheme(currentTheme() === "dark" ? "light" : "dark"), [setTheme]);

  return { theme, toggle, setTheme };
}

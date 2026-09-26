"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "@/lib/theme";

// Sun/moon toggle for the top bar. Shows the icon of the theme you'd switch TO. Uses only tokens.
export default function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      onClick={toggle}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      className="flex h-10 w-10 items-center justify-center rounded-[10px] border border-line2 text-content transition-colors hover:bg-surface"
    >
      {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </button>
  );
}

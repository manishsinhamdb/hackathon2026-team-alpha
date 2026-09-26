// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import ThemeToggle from "@/components/ThemeToggle";
import { StatusPill, RunStatusPill } from "@/components/ui";
import { resolveTheme, THEME_KEY } from "@/lib/theme";

// This jsdom build ships no localStorage; install a minimal in-memory stub so the theme store is testable.
function installLocalStorage() {
  const store = new Map<string, string>();
  const ls = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    get length() {
      return store.size;
    },
  };
  Object.defineProperty(globalThis, "localStorage", { value: ls, configurable: true });
  if (typeof window !== "undefined") Object.defineProperty(window, "localStorage", { value: ls, configurable: true });
}

afterEach(() => cleanup());
beforeEach(() => {
  installLocalStorage();
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("resolveTheme", () => {
  it("honours an explicit stored choice over the OS preference", () => {
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });
  it("falls back to the OS preference when unset or invalid", () => {
    expect(resolveTheme(null, true)).toBe("dark");
    expect(resolveTheme(null, false)).toBe("light");
    expect(resolveTheme("garbage", false)).toBe("light");
  });
});

describe("ThemeToggle", () => {
  it("toggles the <html> data-theme attribute and persists the choice", () => {
    document.documentElement.setAttribute("data-theme", "dark");
    const { getByRole } = render(<ThemeToggle />);
    // Shows the theme you'd switch TO.
    expect(getByRole("button").getAttribute("aria-label")).toMatch(/switch to light/i);

    fireEvent.click(getByRole("button"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");

    fireEvent.click(getByRole("button"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
  });
});

describe("components render under both themes", () => {
  for (const theme of ["dark", "light"] as const) {
    it(`renders status pills with data-theme=${theme}`, () => {
      document.documentElement.setAttribute("data-theme", theme);
      const { container } = render(
        <div>
          <StatusPill status="spec_ready" />
          <StatusPill status="deploying" />
          <RunStatusPill status="failed" />
          <RunStatusPill status="questions" />
        </div>,
      );
      // Token classes are theme-agnostic; both palettes reuse the same class names.
      expect(container.querySelectorAll("span").length).toBeGreaterThanOrEqual(4);
      expect(container.textContent).toContain("SPEC READY");
      expect(container.textContent).toContain("FAILED");
    });
  }
});

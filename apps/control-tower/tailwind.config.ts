import type { Config } from "tailwindcss";

// MongoDB-house / Leafygreen-inspired palette. Semantic tokens are driven by CSS variables
// (see globals.css) so the same class names render correctly in light and dark themes.
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  darkMode: "media", // respect prefers-color-scheme; light is the default
  theme: {
    extend: {
      colors: {
        // Brand
        green: {
          base: "#00ED64", // Leafygreen accent
          dark: "#00A35C",
          darker: "#00684A",
          light: "#E3FCF7",
        },
        ink: {
          DEFAULT: "#001E2B", // near-black header / dark surfaces
          800: "#023430",
          700: "#00494F",
        },
        // Semantic (var-driven for theming; channels so opacity modifiers like bg-ok/12 compose)
        surface: "rgb(var(--surface) / <alpha-value>)",
        "surface-2": "rgb(var(--surface-2) / <alpha-value>)",
        canvas: "rgb(var(--canvas) / <alpha-value>)",
        line: "rgb(var(--line) / <alpha-value>)",
        content: "rgb(var(--content) / <alpha-value>)",
        muted: "rgb(var(--muted) / <alpha-value>)",
        faint: "rgb(var(--faint) / <alpha-value>)",
        accent: "rgb(var(--accent) / <alpha-value>)",
        ok: "rgb(var(--ok) / <alpha-value>)",
        run: "rgb(var(--run) / <alpha-value>)",
        fail: "rgb(var(--fail) / <alpha-value>)",
        idle: "rgb(var(--idle) / <alpha-value>)",
        warnbg: "rgb(var(--warn-bg) / <alpha-value>)",
        warnline: "rgb(var(--warn-line) / <alpha-value>)",
      },
      fontFamily: {
        sans: [
          "Euclid Circular A",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "Helvetica",
          "Arial",
          "sans-serif",
        ],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Monaco", "monospace"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(0, 30, 43, 0.06), 0 1px 3px rgba(0, 30, 43, 0.04)",
        pop: "0 8px 24px rgba(0, 30, 43, 0.16)",
      },
      keyframes: {
        pulse2: { "0%,100%": { opacity: "1" }, "50%": { opacity: "0.35" } },
        blink: { "0%,80%,100%": { opacity: "0.25" }, "40%": { opacity: "1" } },
        "toast-in": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: { "100%": { transform: "translateX(100%)" } },
      },
      animation: {
        pulse2: "pulse2 1.2s ease-in-out infinite",
        blink: "blink 1.4s infinite both",
        "toast-in": "toast-in 160ms ease-out",
      },
    },
  },
  plugins: [],
};

export default config;

import type { Config } from "tailwindcss";

// Control Tower v2 — themeable. Every colour is a CSS variable holding space-separated RGB channels
// (e.g. --ink: 0 30 43), switched by the `data-theme` attribute on <html> (see globals.css). Using the
// `rgb(var(--x) / <alpha-value>)` form keeps Tailwind's opacity modifiers working (e.g. bg-green/10,
// border-fail/40). Dark = the approved v2 palette; light = the MongoDB house palette. No component holds a
// literal hex — swapping the theme only flips the variables. Fonts: Sora (headings), IBM Plex Sans (body),
// IBM Plex Mono (ids), loaded in layout.tsx.
const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: token("ink"), // app background
        surface: token("surface"), // cards / raised panels
        panel: token("panel"), // pipeline column background
        elevated: token("elevated"), // selected row / active session
        line: token("line"), // hairline borders
        line2: token("line2"), // stronger borders / control outlines
        content: token("content"), // primary text
        muted: token("muted"), // secondary text
        faint: token("faint"), // tertiary / captions
        dim: token("dim"), // quietest hint text
        green: token("green"), // primary accent
        greenInk: token("green-ink"), // text on green
        greenDark: token("green-dark"), // user chat bubble
        onGreenDark: token("on-green-dark"), // text on the user chat bubble
        run: token("run"), // running (blue)
        runText: token("run-text"),
        runBg: token("run-bg"),
        amber: token("amber"), // questions
        amberBg: token("amber-bg"),
        fail: token("fail"), // failed
        failBg: token("fail-bg"),
        success: token("success"), // succeeded / healthy
        successBg: token("success-bg"),
      },
      fontFamily: {
        sora: ["Sora", "system-ui", "sans-serif"],
        sans: ['"IBM Plex Sans"', "system-ui", "-apple-system", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
      borderRadius: {
        xl: "14px",
      },
      keyframes: {
        blink: { "0%,80%,100%": { opacity: "0.3" }, "40%": { opacity: "1" } },
        "toast-in": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: { "100%": { transform: "translateX(100%)" } },
        // A stage node flashes when it reaches a terminal state between polls (feature 4).
        flash: {
          "0%,100%": { transform: "scale(1)", filter: "brightness(1)" },
          "30%": { transform: "scale(1.28)", filter: "brightness(1.5)" },
        },
      },
      animation: {
        blink: "blink 1.4s infinite both",
        "toast-in": "toast-in 160ms ease-out",
        flash: "flash 900ms ease-in-out 2",
      },
    },
  },
  plugins: [],
};

export default config;

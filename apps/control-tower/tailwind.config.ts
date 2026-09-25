import type { Config } from "tailwindcss";

// Control Tower v2 — the approved dark design (apps/control-tower/design). A single dark theme:
// near-black ink app background, #112733 cards, the Leafygreen accent #00ED64, and a fixed state palette
// (running blue, questions amber, failed red, success mint). Colours are literal hex — no light theme in
// this round — so the classes map 1:1 to the mockup. Fonts: Sora (headings), IBM Plex Sans (body),
// IBM Plex Mono (ids), loaded in layout.tsx.
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#001E2B", // app background
        surface: "#112733", // cards / raised panels
        panel: "#06232F", // pipeline column background
        elevated: "#16303D", // selected row / active session
        line: "#1C2D38", // hairline borders
        line2: "#3D4F58", // stronger borders / control outlines
        content: "#E8EDEB", // primary text
        muted: "#B8C4C2", // secondary text
        faint: "#889397", // tertiary / captions
        dim: "#5C6C75", // quietest hint text
        green: "#00ED64", // primary accent
        greenInk: "#001E2B", // text on green
        greenDark: "#00684A", // user chat bubble
        run: "#0498EC", // running (blue)
        runText: "#C3E7FE",
        runBg: "#0C3B5B",
        amber: "#FFDD49", // questions
        amberBg: "#3B2A0B",
        fail: "#FF9F97", // failed
        failBg: "#3D1512",
        success: "#71F6BA", // succeeded / healthy
        successBg: "#023430",
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
      },
      animation: {
        blink: "blink 1.4s infinite both",
        "toast-in": "toast-in 160ms ease-out",
      },
    },
  },
  plugins: [],
};

export default config;

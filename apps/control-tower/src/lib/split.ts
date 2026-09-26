// Pure logic for the resizable Conversation | Pipeline split (Round 4, item 1). The chat pane's width is
// stored as a PERCENTAGE of the split container (container-independent, so it survives window resizes), and
// clamped so neither pane drops below MIN_PANE_PX. Kept dependency-free so it unit-tests without React/DOM.
// The width is applied to a CSS variable (`--ct-chat-w`) read by `.ct-split-chat` in globals.css, and set
// before first paint by SPLIT_INIT_SCRIPT so there is no layout flash.

export const SPLIT_KEY = "ct.split.v1";
export const MIN_PANE_PX = 380; // minimum width of EITHER pane
export const KEY_STEP_PX = 16; // arrow-key nudge
// The design's default is the 5/12 · 7/12 chat/pipeline split.
export const DEFAULT_CHAT_PCT = (100 * 5) / 12; // 41.6667

// The smallest chat percentage that still leaves MIN_PANE_PX for the chat pane in a container of this width.
export function minChatPct(containerPx: number): number {
  if (!(containerPx > 0)) return 0;
  return (MIN_PANE_PX / containerPx) * 100;
}

// Clamp a chat percentage so BOTH panes keep at least MIN_PANE_PX. If the container is too narrow to hold
// two minimum panes, fall back to an even split (the layout stacks below lg anyway, so this is a safety net).
export function clampChatPct(pct: number, containerPx: number): number {
  const min = minChatPct(containerPx);
  const max = 100 - min;
  if (!Number.isFinite(pct)) return DEFAULT_CHAT_PCT;
  if (max <= min) return 50;
  return Math.min(max, Math.max(min, pct));
}

// The chat percentage implied by a pointer at `clientX`, given the container's left edge and width.
export function pctFromPointer(clientX: number, containerLeftPx: number, containerPx: number): number {
  if (!(containerPx > 0)) return DEFAULT_CHAT_PCT;
  const raw = ((clientX - containerLeftPx) / containerPx) * 100;
  return clampChatPct(raw, containerPx);
}

// The next chat percentage for a keyboard interaction, or null if the key is not a splitter key.
// Left/Down shrink the chat pane; Right/Up grow it; Home/End go to the min/max; all clamped.
export function nextPctForKey(key: string, current: number, containerPx: number): number | null {
  const step = containerPx > 0 ? (KEY_STEP_PX / containerPx) * 100 : 0;
  switch (key) {
    case "ArrowLeft":
    case "ArrowDown":
      return clampChatPct(current - step, containerPx);
    case "ArrowRight":
    case "ArrowUp":
      return clampChatPct(current + step, containerPx);
    case "Home":
      return clampChatPct(minChatPct(containerPx), containerPx);
    case "End":
      return clampChatPct(100 - minChatPct(containerPx), containerPx);
    default:
      return null;
  }
}

export function loadChatPct(): number {
  if (typeof localStorage === "undefined") return DEFAULT_CHAT_PCT;
  try {
    const v = parseFloat(localStorage.getItem(SPLIT_KEY) || "");
    return Number.isFinite(v) ? v : DEFAULT_CHAT_PCT;
  } catch {
    return DEFAULT_CHAT_PCT;
  }
}

export function saveChatPct(pct: number): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(SPLIT_KEY, String(pct));
  } catch {
    /* storage unavailable — the in-memory state still applies for this session */
  }
}

// Applied in layout.tsx <head> before first paint so the persisted split is honoured with no flash. Mirrors
// loadChatPct(); keep the two in sync. Only sets the variable when a finite value is stored (else the CSS
// default in `.ct-split-chat` takes over).
export const SPLIT_INIT_SCRIPT = `(function(){try{var v=parseFloat(localStorage.getItem('${SPLIT_KEY}'));if(isFinite(v)){document.documentElement.style.setProperty('--ct-chat-w',v+'%');}}catch(e){}})();`;

import { beforeEach, describe, expect, it } from "vitest";
import {
  DEFAULT_CHAT_PCT,
  SPLIT_KEY,
  clampChatPct,
  loadChatPct,
  minChatPct,
  nextPctForKey,
  pctFromPointer,
  saveChatPct,
} from "@/lib/split";

describe("clampChatPct", () => {
  const W = 1600; // minPct = 380/1600*100 = 23.75; maxPct = 76.25

  it("keeps both panes at least the minimum width", () => {
    expect(clampChatPct(10, W)).toBeCloseTo(23.75);
    expect(clampChatPct(90, W)).toBeCloseTo(76.25);
    expect(clampChatPct(50, W)).toBe(50);
  });

  it("falls back to an even split when the container can't fit two minimum panes", () => {
    expect(clampChatPct(30, 700)).toBe(50); // 2 * 380 = 760 > 700
  });

  it("returns the default for a non-finite input", () => {
    expect(clampChatPct(Number.NaN, W)).toBe(DEFAULT_CHAT_PCT);
  });
});

describe("pctFromPointer", () => {
  it("maps the pointer x to a clamped chat percentage", () => {
    expect(pctFromPointer(400, 0, 1000)).toBe(40); // 400/1000
    // min = 38, max = 62 for a 1000px container; a pointer near the edge clamps.
    expect(pctFromPointer(950, 0, 1000)).toBeCloseTo(62);
    expect(pctFromPointer(20, 0, 1000)).toBeCloseTo(38);
  });
  it("accounts for the container's left offset", () => {
    expect(pctFromPointer(600, 200, 1000)).toBe(40); // (600-200)/1000
  });
});

describe("nextPctForKey", () => {
  const W = 1600; // step = 16/1600*100 = 1%

  it("arrow keys nudge by 16px in each direction", () => {
    expect(nextPctForKey("ArrowRight", 50, W)).toBeCloseTo(51);
    expect(nextPctForKey("ArrowUp", 50, W)).toBeCloseTo(51);
    expect(nextPctForKey("ArrowLeft", 50, W)).toBeCloseTo(49);
    expect(nextPctForKey("ArrowDown", 50, W)).toBeCloseTo(49);
  });
  it("Home/End go to the min/max", () => {
    expect(nextPctForKey("Home", 50, W)).toBeCloseTo(minChatPct(W));
    expect(nextPctForKey("End", 50, W)).toBeCloseTo(100 - minChatPct(W));
  });
  it("returns null for a non-splitter key", () => {
    expect(nextPctForKey("a", 50, W)).toBeNull();
  });
});

describe("persist / reset", () => {
  // Minimal localStorage stub so the persist helpers run under the default `node` test environment.
  const store = new Map<string, string>();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).localStorage = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  };
  beforeEach(() => store.clear());

  it("returns the default when nothing is stored (reset behaviour)", () => {
    expect(loadChatPct()).toBe(DEFAULT_CHAT_PCT);
  });
  it("round-trips a saved percentage", () => {
    saveChatPct(55);
    expect(localStorage.getItem(SPLIT_KEY)).toBe("55");
    expect(loadChatPct()).toBe(55);
  });
  it("ignores a corrupt stored value", () => {
    localStorage.setItem(SPLIT_KEY, "not-a-number");
    expect(loadChatPct()).toBe(DEFAULT_CHAT_PCT);
  });
});

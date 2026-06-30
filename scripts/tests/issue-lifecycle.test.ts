import { describe, expect, test } from "bun:test";

import { lifecycle, STALE_UPVOTE_THRESHOLD } from "../issue-lifecycle.ts";

describe("lifecycle", () => {
  test("has the expected labels in order", () => {
    expect(lifecycle.map((l) => l.label)).toEqual([
      "invalid",
      "needs-repro",
      "needs-info",
      "stale",
      "autoclose",
    ]);
  });

  test("labels are unique", () => {
    const labels = lifecycle.map((l) => l.label);
    expect(new Set(labels).size).toBe(labels.length);
  });

  test("every entry has a positive integer days timeout", () => {
    for (const entry of lifecycle) {
      expect(Number.isInteger(entry.days)).toBe(true);
      expect(entry.days).toBeGreaterThan(0);
    }
  });

  test("every entry has a non-empty reason and nudge", () => {
    for (const entry of lifecycle) {
      expect(entry.reason.length).toBeGreaterThan(0);
      expect(entry.nudge.length).toBeGreaterThan(0);
    }
  });

  test("stale and autoclose share the same timeout", () => {
    const stale = lifecycle.find((l) => l.label === "stale")!;
    const autoclose = lifecycle.find((l) => l.label === "autoclose")!;
    expect(stale.days).toBe(autoclose.days);
  });
});

describe("STALE_UPVOTE_THRESHOLD", () => {
  test("is a positive integer", () => {
    expect(Number.isInteger(STALE_UPVOTE_THRESHOLD)).toBe(true);
    expect(STALE_UPVOTE_THRESHOLD).toBeGreaterThan(0);
  });
});

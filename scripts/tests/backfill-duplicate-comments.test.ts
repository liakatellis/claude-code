import { afterEach, beforeEach, describe, expect, test } from "bun:test";

import {
  backfillDuplicateComments,
  triggerDedupeWorkflow,
} from "../backfill-duplicate-comments.ts";
import { makeFetchQueue, type FetchQueue } from "./test-utils.ts";

const ENV_KEYS = ["GITHUB_TOKEN", "DRY_RUN", "MAX_ISSUE_NUMBER", "MIN_ISSUE_NUMBER"] as const;

function snapshotEnv() {
  return Object.fromEntries(ENV_KEYS.map((k) => [k, process.env[k]]));
}

function restoreEnv(snapshot: Record<string, string | undefined>) {
  for (const k of ENV_KEYS) {
    if (snapshot[k] === undefined) delete process.env[k];
    else process.env[k] = snapshot[k];
  }
}

describe("triggerDedupeWorkflow", () => {
  let queue: FetchQueue | undefined;

  afterEach(() => {
    queue?.restore();
    queue = undefined;
  });

  test("dry run logs and makes no fetch call", async () => {
    queue = makeFetchQueue([]);
    await triggerDedupeWorkflow("anthropics", "claude-code", 5, "tok", true);
    expect(queue.calls).toHaveLength(0);
  });

  test("live run POSTs a workflow dispatch with the issue number", async () => {
    queue = makeFetchQueue([{ body: {} }]);
    await triggerDedupeWorkflow("anthropics", "claude-code", 5, "tok", false);

    expect(queue.calls).toHaveLength(1);
    expect(queue.calls[0].url).toBe(
      "https://api.github.com/repos/anthropics/claude-code/actions/workflows/claude-dedupe-issues.yml/dispatches"
    );
    expect(queue.calls[0].init?.method).toBe("POST");
    expect(JSON.parse(queue.calls[0].init?.body as string)).toEqual({
      ref: "main",
      inputs: { issue_number: "5" },
    });
  });
});

describe("backfillDuplicateComments", () => {
  let queue: FetchQueue | undefined;
  let envSnapshot: Record<string, string | undefined>;

  beforeEach(() => {
    envSnapshot = snapshotEnv();
    process.env.GITHUB_TOKEN = "test-token";
  });

  afterEach(() => {
    queue?.restore();
    queue = undefined;
    restoreEnv(envSnapshot);
  });

  test("throws when GITHUB_TOKEN is not set", async () => {
    delete process.env.GITHUB_TOKEN;
    await expect(backfillDuplicateComments()).rejects.toThrow("GITHUB_TOKEN");
  });

  test("dry-runs the workflow trigger by default (no dispatch fetch)", async () => {
    const issue = { number: 100, title: "x", state: "open", user: { id: 1 }, created_at: "2024-01-01" };
    queue = makeFetchQueue([
      { body: [issue] }, // page 1
      { body: [] }, // page 2, stop pagination
      { body: [] }, // comments for issue (no existing dupe comment)
    ]);

    await backfillDuplicateComments();

    expect(queue.calls).toHaveLength(3);
  });

  test("triggers a live dispatch when DRY_RUN=false", async () => {
    process.env.DRY_RUN = "false";
    const issue = { number: 101, title: "x", state: "open", user: { id: 1 }, created_at: "2024-01-01" };
    queue = makeFetchQueue([
      { body: [issue] },
      { body: [] },
      { body: [] }, // comments
      { body: {} }, // POST dispatch
    ]);

    await backfillDuplicateComments();

    expect(queue.calls).toHaveLength(4);
    expect(queue.calls[3].init?.method).toBe("POST");
  });

  test("skips an issue that already has a duplicate-detection comment", async () => {
    const issue = { number: 102, title: "x", state: "open", user: { id: 1 }, created_at: "2024-01-01" };
    const existingDupeComment = {
      id: 1,
      body: "Found a possible duplicate: #1",
      created_at: "2024-01-02",
      user: { type: "Bot", id: 2 },
    };
    queue = makeFetchQueue([
      { body: [issue] },
      { body: [] },
      { body: [existingDupeComment] },
    ]);

    await backfillDuplicateComments();

    // No dispatch call (dry run or otherwise) since the issue was skipped.
    expect(queue.calls).toHaveLength(3);
  });

  test("excludes issues at or above MAX_ISSUE_NUMBER and continues pagination", async () => {
    process.env.MAX_ISSUE_NUMBER = "50";
    const tooHighIssue = { number: 100, title: "x", state: "open", user: { id: 1 }, created_at: "2024-01-01" };
    queue = makeFetchQueue([
      { body: [tooHighIssue] }, // page 1 — oldest (only) issue is >= max, so pagination continues
      { body: [] }, // page 2, stop
    ]);

    await backfillDuplicateComments();

    // The issue was filtered out of range, so no comments fetch ever happens.
    expect(queue.calls).toHaveLength(2);
  });

  test("stops pagination once the oldest issue in a page is below MIN_ISSUE_NUMBER", async () => {
    process.env.MIN_ISSUE_NUMBER = "50";
    const tooLowIssue = { number: 10, title: "x", state: "open", user: { id: 1 }, created_at: "2024-01-01" };
    queue = makeFetchQueue([
      { body: [tooLowIssue] }, // page 1 — oldest issue is below min, pagination breaks immediately
    ]);

    await backfillDuplicateComments();

    expect(queue.calls).toHaveLength(1);
  });
});

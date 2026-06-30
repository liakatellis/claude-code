import { afterEach, beforeEach, describe, expect, test } from "bun:test";

import {
  autoCloseDuplicates,
  closeIssueAsDuplicate,
  extractDuplicateIssueNumber,
} from "../auto-close-duplicates.ts";
import { daysAgo, makeFetchQueue, type FetchQueue } from "./test-utils.ts";

describe("extractDuplicateIssueNumber", () => {
  test("matches #123 format", () => {
    expect(extractDuplicateIssueNumber("Found a possible duplicate: #456")).toBe(456);
  });

  test("matches a GitHub issue URL when there is no # reference", () => {
    expect(
      extractDuplicateIssueNumber(
        "See https://github.com/anthropics/claude-code/issues/789"
      )
    ).toBe(789);
  });

  test("prefers the #123 format over a URL when both are present", () => {
    expect(
      extractDuplicateIssueNumber(
        "Possible duplicate of #111, also see https://github.com/anthropics/claude-code/issues/222"
      )
    ).toBe(111);
  });

  test("returns null when neither pattern matches", () => {
    expect(extractDuplicateIssueNumber("No reference here")).toBeNull();
  });
});

describe("closeIssueAsDuplicate", () => {
  let queue: FetchQueue;

  afterEach(() => {
    queue?.restore();
  });

  test("PATCHes the issue closed and POSTs a duplicate comment", async () => {
    queue = makeFetchQueue([{ body: {} }, { body: {} }]);

    await closeIssueAsDuplicate("anthropics", "claude-code", 42, 7, "tok");

    expect(queue.calls).toHaveLength(2);
    expect(queue.calls[0].url).toBe(
      "https://api.github.com/repos/anthropics/claude-code/issues/42"
    );
    expect(queue.calls[0].init?.method).toBe("PATCH");
    const patchBody = JSON.parse(queue.calls[0].init?.body as string);
    expect(patchBody).toEqual({
      state: "closed",
      state_reason: "duplicate",
      labels: ["duplicate"],
    });

    expect(queue.calls[1].url).toBe(
      "https://api.github.com/repos/anthropics/claude-code/issues/42/comments"
    );
    expect(queue.calls[1].init?.method).toBe("POST");
    const commentBody = JSON.parse(queue.calls[1].init?.body as string);
    expect(commentBody.body).toContain("duplicate of #7");
  });
});

describe("autoCloseDuplicates", () => {
  let queue: FetchQueue | undefined;
  const originalToken = process.env.GITHUB_TOKEN;

  beforeEach(() => {
    process.env.GITHUB_TOKEN = "test-token";
  });

  afterEach(() => {
    queue?.restore();
    queue = undefined;
    if (originalToken === undefined) delete process.env.GITHUB_TOKEN;
    else process.env.GITHUB_TOKEN = originalToken;
  });

  test("throws when GITHUB_TOKEN is not set", async () => {
    delete process.env.GITHUB_TOKEN;
    await expect(autoCloseDuplicates()).rejects.toThrow("GITHUB_TOKEN");
  });

  test("closes an issue whose duplicate comment is old, undisputed, and unresponded to", async () => {
    const issue = {
      number: 1,
      title: "dupe-able issue",
      user: { id: 100 },
      created_at: daysAgo(10),
    };
    const dupeComment = {
      id: 999,
      body: "Found a possible duplicate: #50",
      created_at: daysAgo(5),
      user: { type: "Bot", id: 1 },
    };

    queue = makeFetchQueue([
      { body: [issue] }, // page 1
      { body: [] }, // page 2 (stop pagination)
      { body: [dupeComment] }, // comments for issue #1
      { body: [] }, // reactions on dupe comment
      { body: {} }, // PATCH close
      { body: {} }, // POST close comment
    ]);

    await autoCloseDuplicates();

    expect(queue.calls).toHaveLength(6);
    expect(queue.calls[4].init?.method).toBe("PATCH");
    expect(queue.calls[5].init?.method).toBe("POST");
  });

  test("skips an issue created too recently (filtered out before any per-issue calls)", async () => {
    const recentIssue = {
      number: 2,
      title: "too new",
      user: { id: 100 },
      created_at: daysAgo(1),
    };

    queue = makeFetchQueue([
      { body: [recentIssue] }, // page 1
      { body: [] }, // page 2
    ]);

    await autoCloseDuplicates();

    expect(queue.calls).toHaveLength(2);
  });

  test("skips an issue with no bot duplicate-detection comments", async () => {
    const issue = {
      number: 3,
      title: "no dupe comment",
      user: { id: 100 },
      created_at: daysAgo(10),
    };

    queue = makeFetchQueue([
      { body: [issue] },
      { body: [] },
      { body: [{ id: 1, body: "just a regular comment", created_at: daysAgo(5), user: { type: "User", id: 2 } }] },
    ]);

    await autoCloseDuplicates();

    expect(queue.calls).toHaveLength(3);
  });

  test("skips an issue whose duplicate comment is too recent", async () => {
    const issue = {
      number: 4,
      title: "fresh dupe comment",
      user: { id: 100 },
      created_at: daysAgo(10),
    };
    const dupeComment = {
      id: 1000,
      body: "Found a possible duplicate: #50",
      created_at: daysAgo(1),
      user: { type: "Bot", id: 1 },
    };

    queue = makeFetchQueue([{ body: [issue] }, { body: [] }, { body: [dupeComment] }]);

    await autoCloseDuplicates();

    expect(queue.calls).toHaveLength(3);
  });

  test("skips an issue with human activity after the duplicate comment", async () => {
    const issue = {
      number: 5,
      title: "human replied",
      user: { id: 100 },
      created_at: daysAgo(10),
    };
    const dupeComment = {
      id: 1001,
      body: "Found a possible duplicate: #50",
      created_at: daysAgo(5),
      user: { type: "Bot", id: 1 },
    };
    const humanReply = {
      id: 1002,
      body: "actually not a duplicate",
      created_at: daysAgo(4),
      user: { type: "User", id: 100 },
    };

    queue = makeFetchQueue([
      { body: [issue] },
      { body: [] },
      { body: [dupeComment, humanReply] },
    ]);

    await autoCloseDuplicates();

    expect(queue.calls).toHaveLength(3);
  });

  test("skips an issue when the author thumbs-downed the duplicate comment", async () => {
    const issue = {
      number: 6,
      title: "author disagrees",
      user: { id: 100 },
      created_at: daysAgo(10),
    };
    const dupeComment = {
      id: 1003,
      body: "Found a possible duplicate: #50",
      created_at: daysAgo(5),
      user: { type: "Bot", id: 1 },
    };

    queue = makeFetchQueue([
      { body: [issue] },
      { body: [] },
      { body: [dupeComment] },
      { body: [{ user: { id: 100 }, content: "-1" }] },
    ]);

    await autoCloseDuplicates();

    expect(queue.calls).toHaveLength(4);
  });

  test("skips an issue when no duplicate issue number can be extracted", async () => {
    const issue = {
      number: 7,
      title: "no extractable number",
      user: { id: 100 },
      created_at: daysAgo(10),
    };
    const dupeComment = {
      id: 1004,
      body: "Found a possible duplicate of something, no reference",
      created_at: daysAgo(5),
      user: { type: "Bot", id: 1 },
    };

    queue = makeFetchQueue([
      { body: [issue] },
      { body: [] },
      { body: [dupeComment] },
      { body: [] }, // reactions
    ]);

    await autoCloseDuplicates();

    expect(queue.calls).toHaveLength(4);
  });
});

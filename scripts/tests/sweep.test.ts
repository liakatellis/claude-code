import { afterEach, beforeEach, describe, expect, test } from "bun:test";

import { closeExpired, githubRequest, markStale } from "../sweep.ts";
import { daysAgo, makeFetchQueue, type FetchQueue, type QueuedResponse } from "./test-utils.ts";

const LABELS = ["invalid", "needs-repro", "needs-info", "stale", "autoclose"];

function buildCloseExpiredQueue(targetLabel: string, targetResponses: QueuedResponse[]) {
  const responses: QueuedResponse[] = [];
  for (const label of LABELS) {
    if (label === targetLabel) {
      responses.push(...targetResponses);
    } else {
      responses.push({ body: [] });
    }
  }
  return responses;
}

describe("githubRequest", () => {
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
    await expect(githubRequest("/foo")).rejects.toThrow("GITHUB_TOKEN required");
  });

  test("returns an empty array on 404 instead of throwing", async () => {
    queue = makeFetchQueue([{ ok: false, status: 404, body: {} }]);
    const result = await githubRequest("/repos/x/y/issues/1");
    expect(result).toEqual([]);
  });

  test("throws with status and body text on a non-404 error", async () => {
    queue = makeFetchQueue([{ ok: false, status: 500, body: "server exploded" }]);
    await expect(githubRequest("/foo")).rejects.toThrow(/500.*server exploded/);
  });

  test("sends a bearer token and returns parsed JSON on success", async () => {
    queue = makeFetchQueue([{ body: { hello: "world" } }]);
    const result = await githubRequest("/foo");
    expect(result).toEqual({ hello: "world" });
    expect(queue.calls[0].init?.headers).toMatchObject({
      Authorization: "Bearer test-token",
    });
  });
});

describe("markStale", () => {
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

  test("labels an eligible issue as stale", async () => {
    const issue = { number: 20, updated_at: daysAgo(20), reactions: { "+1": 0 } };
    queue = makeFetchQueue([{ body: [issue] }, { body: {} }, { body: [] }]);

    const labeled = await markStale("anthropics", "claude-code");

    expect(labeled).toBe(1);
    expect(queue.calls).toHaveLength(3);
    expect(queue.calls[1].url).toBe(
      "https://api.github.com/repos/anthropics/claude-code/issues/20/labels"
    );
    expect(queue.calls[1].init?.method).toBe("POST");
    expect(JSON.parse(queue.calls[1].init?.body as string)).toEqual({ labels: ["stale"] });
  });

  test("skips pull requests, locked issues, and assigned issues", async () => {
    const issues = [
      { number: 21, pull_request: {}, updated_at: daysAgo(20) },
      { number: 22, locked: true, updated_at: daysAgo(20) },
      { number: 23, assignees: [{ id: 1 }], updated_at: daysAgo(20) },
    ];
    queue = makeFetchQueue([{ body: issues }, { body: [] }]);

    const labeled = await markStale("anthropics", "claude-code");

    expect(labeled).toBe(0);
    expect(queue.calls).toHaveLength(2);
  });

  test("skips issues already labeled stale or autoclose", async () => {
    const issue = {
      number: 24,
      updated_at: daysAgo(20),
      labels: [{ name: "stale" }],
      reactions: { "+1": 0 },
    };
    queue = makeFetchQueue([{ body: [issue] }, { body: [] }]);

    const labeled = await markStale("anthropics", "claude-code");

    expect(labeled).toBe(0);
    expect(queue.calls).toHaveLength(2);
  });

  test("skips issues at or above the upvote threshold", async () => {
    const issue = { number: 25, updated_at: daysAgo(20), reactions: { "+1": 15 } };
    queue = makeFetchQueue([{ body: [issue] }, { body: [] }]);

    const labeled = await markStale("anthropics", "claude-code");

    expect(labeled).toBe(0);
    expect(queue.calls).toHaveLength(2);
  });

  test("stops pagination when the issues endpoint returns 404", async () => {
    // Regression: a 404 used to return {} whose undefined .length never
    // triggered the empty-page break, looping until the page cap.
    queue = makeFetchQueue([{ ok: false, status: 404, body: {} }]);

    const labeled = await markStale("anthropics", "claude-code");

    expect(labeled).toBe(0);
    expect(queue.calls).toHaveLength(1);
  });

  test("stops as soon as it reaches an issue updated within the cutoff (ascending sort)", async () => {
    const issues = [
      { number: 26, updated_at: daysAgo(20), reactions: { "+1": 0 } },
      { number: 27, updated_at: daysAgo(2), reactions: { "+1": 0 } },
    ];
    queue = makeFetchQueue([{ body: issues }, { body: {} }]);

    const labeled = await markStale("anthropics", "claude-code");

    expect(labeled).toBe(1);
    // Only the page fetch + the label POST for #26 — no second page fetch,
    // because the loop returns the moment it sees #27's recent updated_at.
    expect(queue.calls).toHaveLength(2);
  });
});

describe("closeExpired", () => {
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

  test("closes an issue whose lifecycle label is old enough with no human follow-up", async () => {
    const issue = { number: 10, reactions: { "+1": 0 } };
    const events = [{ event: "labeled", label: { name: "stale" }, created_at: daysAgo(20) }];

    const responses = buildCloseExpiredQueue("stale", [
      { body: [issue] },
      { body: events },
      { body: [] }, // comments since labeled
      { body: {} }, // POST close comment
      { body: {} }, // PATCH close
      { body: [] }, // page 2 for "stale" — stop pagination
    ]);
    queue = makeFetchQueue(responses);

    const closed = await closeExpired("anthropics", "claude-code");

    expect(closed).toBe(1);
    expect(queue.calls).toHaveLength(10);
    const postCall = queue.calls[6];
    const patchCall = queue.calls[7];
    expect(postCall.init?.method).toBe("POST");
    expect(JSON.parse(postCall.init?.body as string).body).toContain("inactive for too long");
    expect(patchCall.init?.method).toBe("PATCH");
    expect(JSON.parse(patchCall.init?.body as string)).toEqual({
      state: "closed",
      state_reason: "not_planned",
    });
  });

  test("skips closing when a human commented after the label was applied", async () => {
    const issue = { number: 11, reactions: { "+1": 0 } };
    const events = [{ event: "labeled", label: { name: "stale" }, created_at: daysAgo(20) }];
    const humanComment = [{ user: { type: "User" }, created_at: daysAgo(10) }];

    const responses = buildCloseExpiredQueue("stale", [
      { body: [issue] },
      { body: events },
      { body: humanComment },
      { body: [] }, // page 2 stop
    ]);
    queue = makeFetchQueue(responses);

    const closed = await closeExpired("anthropics", "claude-code");

    expect(closed).toBe(0);
    expect(queue.calls).toHaveLength(8);
  });

  test("skips an issue with no matching labeled event", async () => {
    const issue = { number: 12, reactions: { "+1": 0 } };

    const responses = buildCloseExpiredQueue("stale", [
      { body: [issue] },
      { body: [] }, // no labeled event found
      { body: [] }, // page 2 stop
    ]);
    queue = makeFetchQueue(responses);

    const closed = await closeExpired("anthropics", "claude-code");

    expect(closed).toBe(0);
    expect(queue.calls).toHaveLength(7);
  });

  test("skips issues at or above the upvote threshold without fetching events", async () => {
    const issue = { number: 13, reactions: { "+1": 99 } };

    const responses = buildCloseExpiredQueue("stale", [
      { body: [issue] },
      { body: [] }, // page 2 stop
    ]);
    queue = makeFetchQueue(responses);

    const closed = await closeExpired("anthropics", "claude-code");

    expect(closed).toBe(0);
    expect(queue.calls).toHaveLength(6);
  });
});

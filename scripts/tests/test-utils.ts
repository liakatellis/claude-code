// Shared helpers for scripts/tests/*.test.ts.
//
// `makeFetchQueue` replaces `globalThis.fetch` with a stub that returns
// canned responses in order, so scripts that call the real GitHub API via
// `fetch` can be driven deterministically in tests. Each queued response is
// consumed exactly once; calling fetch more times than there are queued
// responses throws, so tests fail loudly if a script's call sequence drifts.

export interface QueuedResponse {
  body?: unknown;
  ok?: boolean;
  status?: number;
  statusText?: string;
}

export interface FetchCall {
  url: string;
  init?: RequestInit;
}

export interface FetchQueue {
  calls: FetchCall[];
  restore: () => void;
}

export function makeFetchQueue(responses: QueuedResponse[]): FetchQueue {
  const calls: FetchCall[] = [];
  let index = 0;
  const original = globalThis.fetch;

  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (index >= responses.length) {
      throw new Error(
        `fetch called more times than expected (call #${calls.length}, url: ${url})`
      );
    }
    const queued = responses[index++];
    const ok = queued.ok ?? true;
    return {
      ok,
      status: queued.status ?? (ok ? 200 : 500),
      statusText: queued.statusText ?? (ok ? "OK" : "Error"),
      json: async () => queued.body,
      text: async () =>
        typeof queued.body === "string" ? queued.body : JSON.stringify(queued.body),
    } as unknown as Response;
  }) as typeof fetch;

  return {
    calls,
    restore() {
      globalThis.fetch = original;
    },
  };
}

export function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString();
}

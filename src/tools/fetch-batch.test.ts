import { describe, expect, it, vi } from "vitest";

import type { FetchResponse } from "../types.js";
import { fetchBatchWithWorker } from "./fetch-batch.js";

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
} {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((innerResolve) => {
    resolve = innerResolve;
  });
  return { promise, resolve };
}

describe("fetchBatchWithWorker", () => {
  it("submits all unique URLs immediately, deduplicates, and preserves input order", async () => {
    const pending = new Map<
      string,
      ReturnType<typeof deferred<FetchResponse>>
    >();
    const worker = vi.fn(({ url }: { url: string }) => {
      const task = deferred<FetchResponse>();
      pending.set(url, task);
      return task.promise;
    });
    const progress: number[] = [];

    const batchPromise = fetchBatchWithWorker(
      {
        urls: [
          "https://a.example/page",
          "https://b.example/page",
          "https://a.example/page",
        ],
        format: "text",
        timeout: 1_000,
      },
      worker,
      (completed) => progress.push(completed)
    );

    await vi.waitFor(() => expect(worker).toHaveBeenCalledTimes(2));

    pending.get("https://b.example/page")!.resolve({
      success: true,
      content: "b",
      title: "B",
      url: "https://b.example/page",
      status: 200,
    });
    pending.get("https://a.example/page")!.resolve({
      success: false,
      error: {
        code: "TIMEOUT",
        message: "timed out",
      },
      url: "https://a.example/page",
    });

    const result = await batchPromise;

    expect(result).toMatchObject({
      total: 3,
      succeeded: 1,
      failed: 1,
    });
    expect(result.results.map((item) => item.url)).toEqual([
      "https://a.example/page",
      "https://b.example/page",
      "https://a.example/page",
    ]);
    expect(result.results[0]).toBe(result.results[2]);
    expect(progress).toEqual([1, 3]);
  });

  it("converts an unexpected worker rejection into a batch error result", async () => {
    const result = await fetchBatchWithWorker(
      {
        urls: ["https://a.example/page"],
        format: "text",
        timeout: 1_000,
      },
      async () => {
        throw new Error("worker crashed");
      }
    );

    expect(result.failed).toBe(1);
    expect(result.results[0]).toMatchObject({
      success: false,
      error: {
        code: "UNKNOWN",
        message: "Unexpected error: worker crashed",
      },
    });
  });
});

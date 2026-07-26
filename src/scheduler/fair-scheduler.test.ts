import { describe, expect, it } from "vitest";

import {
  FairTaskScheduler,
  SchedulerAbortedError,
  SchedulerClosedError,
  SchedulerQueueTimeoutError,
} from "./fair-scheduler.js";

describe("FairTaskScheduler", () => {
  it("enforces global and per-key concurrency", async () => {
    const scheduler = new FairTaskScheduler({
      maxConcurrent: 2,
      maxConcurrentPerKey: 1,
      defaultQueueTimeoutMs: 1_000,
    });

    const firstA = await scheduler.acquire("a.example");
    const secondAPromise = scheduler.acquire("a.example");
    const firstB = await scheduler.acquire("b.example");

    expect(scheduler.stats.running).toBe(2);
    expect(scheduler.stats.queued).toBe(1);
    expect(scheduler.stats.activeByKey).toEqual({
      "a.example": 1,
      "b.example": 1,
    });

    firstB.release();
    expect(scheduler.stats.running).toBe(1);
    expect(scheduler.stats.queued).toBe(1);

    firstA.release();
    const secondA = await secondAPromise;
    expect(scheduler.stats.running).toBe(1);
    expect(scheduler.stats.queued).toBe(0);

    secondA.release();
    expect(scheduler.stats.running).toBe(0);
  });

  it("rotates across keys instead of letting one key monopolise the queue", async () => {
    const scheduler = new FairTaskScheduler({
      maxConcurrent: 1,
      maxConcurrentPerKey: 1,
    });
    const order: string[] = [];

    const firstA = await scheduler.acquire("a.example");
    const secondAPromise = scheduler.acquire("a.example").then((lease) => {
      order.push("a2");
      return lease;
    });
    const firstBPromise = scheduler.acquire("b.example").then((lease) => {
      order.push("b1");
      return lease;
    });

    firstA.release();
    const firstB = await firstBPromise;
    expect(order).toEqual(["b1"]);

    firstB.release();
    const secondA = await secondAPromise;
    expect(order).toEqual(["b1", "a2"]);
    secondA.release();
  });

  it("rejects a queued request after its queue timeout", async () => {
    const scheduler = new FairTaskScheduler({
      maxConcurrent: 1,
      maxConcurrentPerKey: 1,
      defaultQueueTimeoutMs: 1_000,
    });
    const lease = await scheduler.acquire("a.example");

    await expect(
      scheduler.acquire("a.example", { timeoutMs: 10 })
    ).rejects.toBeInstanceOf(SchedulerQueueTimeoutError);

    lease.release();
    expect(scheduler.stats).toMatchObject({ running: 0, queued: 0 });
  });

  it("removes an aborted queued request without leaking capacity", async () => {
    const scheduler = new FairTaskScheduler({
      maxConcurrent: 1,
      maxConcurrentPerKey: 1,
    });
    const lease = await scheduler.acquire("a.example");
    const controller = new AbortController();
    const queued = scheduler.acquire("a.example", {
      signal: controller.signal,
    });

    controller.abort();
    await expect(queued).rejects.toBeInstanceOf(SchedulerAbortedError);
    expect(scheduler.stats).toMatchObject({ running: 1, queued: 0 });

    lease.release();
    expect(scheduler.stats.running).toBe(0);
  });

  it("makes release idempotent", async () => {
    const scheduler = new FairTaskScheduler({
      maxConcurrent: 1,
      maxConcurrentPerKey: 1,
    });
    const lease = await scheduler.acquire("a.example");

    lease.release();
    lease.release();

    expect(scheduler.stats).toMatchObject({ running: 0, queued: 0 });
  });

  it("rejects queued and future work when closed", async () => {
    const scheduler = new FairTaskScheduler({
      maxConcurrent: 1,
      maxConcurrentPerKey: 1,
    });
    const lease = await scheduler.acquire("a.example");
    const queued = scheduler.acquire("b.example");

    scheduler.close();

    await expect(queued).rejects.toBeInstanceOf(SchedulerClosedError);
    await expect(scheduler.acquire("c.example")).rejects.toBeInstanceOf(
      SchedulerClosedError
    );

    lease.release();
  });
});

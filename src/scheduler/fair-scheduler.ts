/**
 * Fair, bounded scheduler for expensive browser/Python workers.
 *
 * Guarantees:
 * - global and per-key concurrency limits;
 * - FIFO order inside each key;
 * - round-robin-style fairness across keys;
 * - queue cancellation and queue wait timeouts;
 * - idempotent lease release;
 * - automatic release after task success or failure through run().
 */

export interface FairTaskSchedulerOptions {
  maxConcurrent: number;
  maxConcurrentPerKey: number;
  defaultQueueTimeoutMs?: number;
}

export interface SchedulerAcquireOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
}

export interface SchedulerLease {
  release(): void;
}

export interface SchedulerStats {
  running: number;
  queued: number;
  maxConcurrent: number;
  maxConcurrentPerKey: number;
  activeByKey: Record<string, number>;
  queuedByKey: Record<string, number>;
}

export class SchedulerQueueTimeoutError extends Error {
  constructor(key: string, timeoutMs: number) {
    super(`Pool queue timeout for ${key} after ${timeoutMs}ms`);
    this.name = "SchedulerQueueTimeoutError";
  }
}

export class SchedulerAbortedError extends Error {
  constructor(key: string) {
    super(`Pool queue request aborted for ${key}`);
    this.name = "SchedulerAbortedError";
  }
}

export class SchedulerClosedError extends Error {
  constructor() {
    super("Pool scheduler is closed");
    this.name = "SchedulerClosedError";
  }
}

interface QueueEntry {
  id: number;
  key: string;
  sequence: number;
  resolve: (lease: SchedulerLease) => void;
  reject: (error: Error) => void;
  signal?: AbortSignal;
  abortListener?: () => void;
  timeoutHandle?: NodeJS.Timeout;
}

export class FairTaskScheduler {
  private readonly maxConcurrent: number;
  private readonly maxConcurrentPerKey: number;
  private readonly defaultQueueTimeoutMs: number;
  private readonly queue: QueueEntry[] = [];
  private readonly activeByKey = new Map<string, number>();
  private readonly lastStartedByKey = new Map<string, number>();
  private running = 0;
  private nextEntryId = 1;
  private nextSequence = 1;
  private startSequence = 0;
  private closed = false;

  constructor(options: FairTaskSchedulerOptions) {
    this.maxConcurrent = requirePositiveInteger(
      options.maxConcurrent,
      "maxConcurrent"
    );
    this.maxConcurrentPerKey = requirePositiveInteger(
      options.maxConcurrentPerKey,
      "maxConcurrentPerKey"
    );
    this.defaultQueueTimeoutMs = requireNonNegativeInteger(
      options.defaultQueueTimeoutMs ?? 30_000,
      "defaultQueueTimeoutMs"
    );
  }

  acquire(
    rawKey: string,
    options: SchedulerAcquireOptions = {}
  ): Promise<SchedulerLease> {
    if (this.closed) {
      return Promise.reject(new SchedulerClosedError());
    }

    const key = normalizeKey(rawKey);
    if (options.signal?.aborted) {
      return Promise.reject(new SchedulerAbortedError(key));
    }

    const timeoutMs = requireNonNegativeInteger(
      options.timeoutMs ?? this.defaultQueueTimeoutMs,
      "timeoutMs"
    );

    return new Promise<SchedulerLease>((resolve, reject) => {
      const entry: QueueEntry = {
        id: this.nextEntryId++,
        key,
        sequence: this.nextSequence++,
        resolve,
        reject,
        signal: options.signal,
      };

      if (options.signal) {
        entry.abortListener = () => {
          this.rejectQueuedEntry(entry.id, new SchedulerAbortedError(key));
        };
        options.signal.addEventListener("abort", entry.abortListener, {
          once: true,
        });
      }

      if (timeoutMs > 0) {
        entry.timeoutHandle = setTimeout(() => {
          this.rejectQueuedEntry(
            entry.id,
            new SchedulerQueueTimeoutError(key, timeoutMs)
          );
        }, timeoutMs);
      }

      this.queue.push(entry);
      this.dispatch();
    });
  }

  async run<T>(
    key: string,
    task: () => Promise<T> | T,
    options: SchedulerAcquireOptions = {}
  ): Promise<T> {
    const lease = await this.acquire(key, options);
    try {
      return await task();
    } finally {
      lease.release();
    }
  }

  close(): void {
    if (this.closed) {
      return;
    }
    this.closed = true;
    const pending = this.queue.splice(0);
    for (const entry of pending) {
      this.cleanupEntry(entry);
      entry.reject(new SchedulerClosedError());
    }
  }

  get stats(): SchedulerStats {
    const queuedByKey: Record<string, number> = {};
    for (const entry of this.queue) {
      queuedByKey[entry.key] = (queuedByKey[entry.key] ?? 0) + 1;
    }

    return {
      running: this.running,
      queued: this.queue.length,
      maxConcurrent: this.maxConcurrent,
      maxConcurrentPerKey: this.maxConcurrentPerKey,
      activeByKey: Object.fromEntries(this.activeByKey),
      queuedByKey,
    };
  }

  private dispatch(): void {
    while (!this.closed && this.running < this.maxConcurrent) {
      const entryIndex = this.selectRunnableEntryIndex();
      if (entryIndex < 0) {
        return;
      }

      const [entry] = this.queue.splice(entryIndex, 1);
      this.cleanupEntry(entry);
      this.running += 1;
      this.activeByKey.set(
        entry.key,
        (this.activeByKey.get(entry.key) ?? 0) + 1
      );
      this.lastStartedByKey.set(entry.key, ++this.startSequence);

      let released = false;
      entry.resolve({
        release: () => {
          if (released) {
            return;
          }
          released = true;
          this.running = Math.max(0, this.running - 1);
          const remaining = (this.activeByKey.get(entry.key) ?? 1) - 1;
          if (remaining > 0) {
            this.activeByKey.set(entry.key, remaining);
          } else {
            this.activeByKey.delete(entry.key);
          }
          this.dispatch();
        },
      });
    }
  }

  private selectRunnableEntryIndex(): number {
    let selectedIndex = -1;
    let selectedLastStarted = Number.POSITIVE_INFINITY;
    let selectedSequence = Number.POSITIVE_INFINITY;

    for (let index = 0; index < this.queue.length; index += 1) {
      const entry = this.queue[index];
      const activeForKey = this.activeByKey.get(entry.key) ?? 0;
      if (activeForKey >= this.maxConcurrentPerKey) {
        continue;
      }

      const lastStarted = this.lastStartedByKey.get(entry.key) ?? -1;
      if (
        lastStarted < selectedLastStarted ||
        (lastStarted === selectedLastStarted &&
          entry.sequence < selectedSequence)
      ) {
        selectedIndex = index;
        selectedLastStarted = lastStarted;
        selectedSequence = entry.sequence;
      }
    }

    return selectedIndex;
  }

  private rejectQueuedEntry(id: number, error: Error): void {
    const index = this.queue.findIndex((entry) => entry.id === id);
    if (index < 0) {
      return;
    }

    const [entry] = this.queue.splice(index, 1);
    this.cleanupEntry(entry);
    entry.reject(error);
    this.dispatch();
  }

  private cleanupEntry(entry: QueueEntry): void {
    if (entry.timeoutHandle) {
      clearTimeout(entry.timeoutHandle);
    }
    if (entry.signal && entry.abortListener) {
      entry.signal.removeEventListener("abort", entry.abortListener);
    }
  }
}

function normalizeKey(key: string): string {
  const normalized = key.trim().toLowerCase();
  if (!normalized) {
    throw new Error("Scheduler key cannot be empty");
  }
  return normalized;
}

function requirePositiveInteger(value: number, name: string): number {
  if (!Number.isInteger(value) || value <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
  return value;
}

function requireNonNegativeInteger(value: number, name: string): number {
  if (!Number.isInteger(value) || value < 0) {
    throw new Error(`${name} must be a non-negative integer`);
  }
  return value;
}

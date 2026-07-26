/**
 * Fair-scheduled public fetch boundary.
 *
 * The existing fetch.ts implementation remains responsible for rate limiting,
 * retries, Python process lifecycle, Chrome cleanup, and response conversion.
 * This layer adds fair per-domain admission before that expensive work starts.
 */

import type {
  ContentFormat,
  FetchOptions,
  FetchResponse,
} from "../types.js";
import {
  createErrorResponse,
  getDefaultConfig,
} from "../types.js";
import { extractDomain } from "../rate-limit/limiter.js";
import { FairTaskScheduler } from "../scheduler/fair-scheduler.js";
import { logger } from "../utils/logger.js";
import {
  fetch as fetchUnscheduled,
  fetchPage as fetchPageUnscheduled,
} from "./fetch.js";

const config = getDefaultConfig();
const maxPerDomain = parsePositiveInteger(
  process.env.TURBOFETCH_MAX_PROCESSES_PER_DOMAIN,
  2
);
const queueTimeoutMs = parseNonNegativeInteger(
  process.env.TURBOFETCH_QUEUE_TIMEOUT,
  30_000
);

const processScheduler = new FairTaskScheduler({
  maxConcurrent: config.python.maxProcesses,
  maxConcurrentPerKey: Math.min(config.python.maxProcesses, maxPerDomain),
  defaultQueueTimeoutMs: queueTimeoutMs,
});

export async function fetchPage(
  options: FetchOptions
): Promise<FetchResponse> {
  const domain = extractDomain(options.url);

  try {
    logger.info("scheduler_acquire", {
      url: options.url,
      domain,
      ...schedulerLogFields(domain),
    });
    const lease = await processScheduler.acquire(domain);

    try {
      logger.info("scheduler_acquired", {
        url: options.url,
        domain,
        ...schedulerLogFields(domain),
      });
      return await fetchPageUnscheduled(options);
    } finally {
      lease.release();
      logger.info("scheduler_released", {
        url: options.url,
        domain,
        ...schedulerLogFields(domain),
      });
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    logger.error("scheduler_rejected", {
      url: options.url,
      domain,
      event: message,
    });
    return createErrorResponse(options.url, "POOL_EXHAUSTED", message);
  }
}

export async function fetch(
  url: string,
  options: {
    format?: ContentFormat;
    wait_for?: string;
    timeout?: number;
    human_mode?: boolean;
  } = {}
): Promise<FetchResponse> {
  if (
    options.format === undefined &&
    options.wait_for === undefined &&
    options.timeout === undefined &&
    options.human_mode === undefined
  ) {
    return fetchPage({
      url,
      format: "text",
      timeout: config.timeouts.navigation,
    });
  }

  return fetchPage({
    url,
    format: options.format ?? "text",
    wait_for: options.wait_for,
    timeout: options.timeout ?? config.timeouts.navigation,
    human_mode: options.human_mode,
  });
}

export function getProcessSchedulerStats() {
  return processScheduler.stats;
}

/** Raw compatibility export for callers that explicitly need the old path. */
export const fetchWithoutFairScheduling = fetchUnscheduled;

function schedulerLogFields(domain: string) {
  const stats = processScheduler.stats;
  return {
    scheduler_running: stats.running,
    scheduler_queued: stats.queued,
    scheduler_max: stats.maxConcurrent,
    scheduler_per_domain_max: stats.maxConcurrentPerKey,
    scheduler_domain_running: stats.activeByKey[domain] ?? 0,
    scheduler_domain_queued: stats.queuedByKey[domain] ?? 0,
  };
}

function parsePositiveInteger(
  value: string | undefined,
  fallback: number
): number {
  const parsed = Number.parseInt(value ?? "", 10);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function parseNonNegativeInteger(
  value: string | undefined,
  fallback: number
): number {
  const parsed = Number.parseInt(value ?? "", 10);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : fallback;
}

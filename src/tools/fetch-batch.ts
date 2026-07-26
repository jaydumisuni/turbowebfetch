/**
 * Batch URL fetch implementation.
 *
 * All unique URLs are submitted immediately. The shared fair scheduler controls
 * actual browser/Python concurrency, avoiding fixed chunk barriers while
 * preserving input order and deduplicating identical URLs.
 */

import type {
  ContentFormat,
  FetchBatchOptions,
  FetchBatchResult,
  FetchOptions,
  FetchResponse,
} from "../types.js";
import { getDefaultConfig, isSuccessResponse } from "../types.js";
import { logger } from "../utils/logger.js";
import { fetchPage } from "./fetch-scheduled.js";

const config = getDefaultConfig();

export type BatchFetchWorker = (
  options: FetchOptions
) => Promise<FetchResponse>;

export async function fetchBatchWithWorker(
  options: FetchBatchOptions,
  worker: BatchFetchWorker,
  onProgress?: (completed: number, total: number) => void
): Promise<FetchBatchResult> {
  const startTime = Date.now();
  const { urls, format, timeout, human_mode } = options;
  const total = urls.length;

  if (total === 0) {
    return {
      results: [],
      total: 0,
      succeeded: 0,
      failed: 0,
    };
  }

  const indicesByUrl = new Map<string, number[]>();
  const uniqueUrls: string[] = [];
  urls.forEach((url, index) => {
    const indices = indicesByUrl.get(url);
    if (indices) {
      indices.push(index);
    } else {
      indicesByUrl.set(url, [index]);
      uniqueUrls.push(url);
    }
  });

  if (uniqueUrls.length < total) {
    logger.info("batch_deduplicated", {
      event: `Deduplicated ${total} URLs to ${uniqueUrls.length} unique URLs`,
    });
  }

  const results = new Array<FetchResponse>(total);
  let succeeded = 0;
  let failed = 0;
  let completed = 0;

  logger.info("batch_fetch_start", {
    event: `Scheduling ${uniqueUrls.length} unique URLs`,
    format,
    queue_length: uniqueUrls.length,
  });

  await Promise.all(
    uniqueUrls.map(async (url) => {
      const result = await runWorkerSafely(worker, {
        url,
        format: format as ContentFormat,
        timeout,
        human_mode,
      });
      const originalIndices = indicesByUrl.get(url) ?? [];

      for (const originalIndex of originalIndices) {
        results[originalIndex] = result;
      }

      if (isSuccessResponse(result)) {
        succeeded += 1;
      } else {
        failed += 1;
      }

      completed += originalIndices.length;
      onProgress?.(completed, total);
    })
  );

  logger.info("batch_fetch_complete", {
    event: `Batch fetch completed: ${succeeded}/${uniqueUrls.length} succeeded`,
    duration_ms: Date.now() - startTime,
  });

  return {
    results,
    total,
    succeeded,
    failed,
  };
}

export async function fetchBatch(
  options: FetchBatchOptions
): Promise<FetchBatchResult> {
  return fetchBatchWithWorker(options, fetchPage);
}

export async function fetchMultiple(
  urls: string[],
  options: {
    format?: ContentFormat;
    timeout?: number;
    human_mode?: boolean;
  } = {}
): Promise<FetchBatchResult> {
  return fetchBatch({
    urls,
    format: options.format ?? "text",
    timeout: options.timeout ?? config.timeouts.navigation,
    human_mode: options.human_mode,
  });
}

export async function fetchBatchWithProgress(
  options: FetchBatchOptions,
  onProgress?: (completed: number, total: number) => void
): Promise<FetchBatchResult> {
  return fetchBatchWithWorker(options, fetchPage, onProgress);
}

async function runWorkerSafely(
  worker: BatchFetchWorker,
  options: FetchOptions
): Promise<FetchResponse> {
  try {
    return await worker(options);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    logger.error("batch_uncaught_error", {
      url: options.url,
      event: `Uncaught error: ${message}`,
    });
    return {
      success: false,
      error: {
        code: "UNKNOWN",
        message: `Unexpected error: ${message}`,
      },
      url: options.url,
    };
  }
}

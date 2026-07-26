/**
 * Tool exports for the TurboFetch MCP Server.
 *
 * Public fetch operations pass through the fair scheduler. The underlying raw
 * fetch implementation remains available only through an explicit compatibility
 * export from fetch-scheduled.ts.
 */

export {
  fetchPage,
  fetch,
  getProcessSchedulerStats,
  fetchWithoutFairScheduling,
} from "./fetch-scheduled.js";

export {
  fetchBatch,
  fetchMultiple,
  fetchBatchWithProgress,
  fetchBatchWithWorker,
} from "./fetch-batch.js";

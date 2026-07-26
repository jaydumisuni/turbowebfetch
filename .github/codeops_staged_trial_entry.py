"""Entry point that applies reviewed trial policy without authoring product code."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import codeops_live_provider

_original_compress = codeops_live_provider.compress_codeops_task
_context_number = 0


def _compress_with_nodenext_rule(task: str, **kwargs):
    """Add reviewed rules and preserve the exact bounded provider request as evidence."""
    global _context_number
    payload = json.loads(task)
    rules = payload.get("rules")
    if not isinstance(rules, list):
        rules = []
        payload["rules"] = rules
    rules.extend(
        (
            "This repository uses NodeNext. Every relative import written in TypeScript, including tests, must use the runtime .js extension.",
            "Never emit a replace operation whose old and new text are identical. When the named line is already correct, diagnose the remaining proof failure from the supplied evidence and current file content instead.",
        )
    )
    objective = str(payload.get("objective", "")).lower()
    if "proof surface" in objective:
        rules.extend(
            (
                "The initial proposal must include src/rate-limit/limiter.test.ts with deterministic Vitest assertions for confirmed extractDomain behaviour. Do not defer this test file to a correction pass because corrections cannot expand the approved file scope.",
                "Ground extractDomain tests in observed current behaviour: full http/https URLs and schemeless hostnames without a port are valid; uppercase full URLs are lowercased.",
                "Do not assert extractDomain('sub.example.com:3000') returns sub.example.com because URL parsing treats the prefix as a non-http scheme and currently returns an empty hostname.",
                "Do not assert extractDomain('http://') throws because the fallback currently accepts it as hostname 'http'. Use only confirmed invalid inputs such as the empty string, whitespace-only input, or '://:'.",
            )
        )
    if "scheduler core" in objective:
        rules.extend(
            (
                "The initial scheduler implementation must satisfy the repository's existing ESLint rules: do not use explicit any, omit or underscore unused callback arguments, and use const for bindings that are never reassigned.",
                "Use generics and unknown instead of any in queues, errors, and helper methods while preserving type safety.",
                "Queue-wait timeout measures only time before work starts. Clear its timer when the task starts and never time out or release capacity for already-running work.",
                "AbortSignal cancellation must remove and reject queued work before start. Once work starts, do not release scheduler capacity until the worker promise settles; the worker may independently observe the signal.",
                "Invoke caller work through a promise boundary or explicit try/catch so a synchronous throw becomes a rejected task and capacity is released exactly once in finally.",
                "Tests for cancellation and queue timeout must first occupy the relevant capacity so the tested task is genuinely queued. Fairness tests must assert early cross-domain progress, not merely final task counts.",
                "In cancellation and queue-timeout tests, never await the blocker before scheduling the candidate. Keep a blocker promise unresolved, confirm its worker started through a deferred barrier, schedule the candidate while capacity remains occupied, observe cancellation or timeout, then release and await the blocker.",
                "If proof shows timedOut or cancelled is undefined because the candidate ran normally, correct the test's blocker sequencing rather than changing scheduler timeout semantics.",
                "Validate global and per-domain concurrency limits as positive integers and reject invalid scheduler configuration.",
                "Do not declare a local loop-control variable that is assigned but never read, such as started. When one task is started per scheduling pass, start it and return or break directly without an unused flag.",
                "Never modify .eslintrc.cjs, package.json, or another proof configuration to hide a scheduler-source lint failure. Correct the named scheduler source or scheduler test inside the approved stage scope.",
            )
        )
    if "integrate the existing scheduler core" in objective:
        rules.extend(
            (
                "Modify the canonical src/tools/fetch-batch.ts implementation. Do not create src/tools/batchFetch.ts, src/tools/batch-fetch.ts, or any parallel batch-fetch module.",
                "The initial proposal must include src/tools/fetch-batch.test.ts so all later corrections remain inside the originally approved scope. Do not place batch tests under tests/.",
                "Use the existing fetchPage import from ./fetch.js. Do not import fetchPage, a semaphore, a rate limiter, or response types from invented repository paths.",
                "Use the actual FetchResponse, FetchBatchResult, FetchBatchOptions, ContentFormat, and isSuccessResponse contracts from ../types.js. A response uses success, not ok, and failures contain a nested error object.",
                "Do not directly acquire or release the token-bucket limiter or Python process semaphore. The real fetchPage path already owns those separate safety boundaries.",
                "Import and use the previously generated scheduler from ./scheduler.js. Do not duplicate scheduler logic inside fetch-batch.ts.",
                "Preserve the public fetchBatch, fetchMultiple, and fetchBatchWithProgress exports and their existing option shapes.",
                "Deduplicate work by exact URL, execute each unique URL once, then place the same FetchResponse at every original index. The returned results length and ordering must match the input.",
                "Preserve existing succeeded and failed counting semantics: count each unique URL once, not each duplicate output slot.",
                "Convert an unexpected thrown fetchPage error into a FetchResponse failure for that URL and allow all remaining scheduled work to settle.",
                "Write deterministic Vitest tests that mock ./fetch.js before importing fetch-batch.ts. Tests must never launch Chrome or Python and must prove dynamic scheduling, duplicate coalescing, original ordering, and worker-failure isolation.",
                "Satisfy the existing ESLint and strict TypeScript rules without explicit any or unused imports.",
            )
        )
    compressed, metadata = _original_compress(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        **kwargs,
    )
    _context_number += 1
    evidence_dir = Path("codeops-staged-evidence") / "provider-contexts"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / f"provider-task-{_context_number:03d}.json").write_text(
        compressed,
        encoding="utf-8",
    )
    (evidence_dir / f"provider-task-{_context_number:03d}-metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return compressed, metadata


codeops_live_provider.compress_codeops_task = _compress_with_nodenext_rule

import codeops_staged_trial

_scheduler_context = (
    "src/tools/scheduler.ts",
    "src/scheduler/Scheduler.ts",
    "src/scheduler/Scheduler.test.ts",
    "src/scheduler/scheduler.ts",
    "src/scheduler/scheduler.test.ts",
    "src/utils/scheduler.ts",
    "src/utils/scheduler.test.ts",
    "tests/scheduler.test.ts",
)

_reviewed_stages = []
for stage in codeops_staged_trial.STAGES:
    if stage.id == "proof-gates":
        stage = replace(
            stage,
            path_hints=(
                "src/rate-limit/limiter.test.ts",
                "package.json",
                "src/rate-limit/limiter.ts",
                "tsconfig.json",
                "src/tools/fetch-batch.ts",
            ),
            max_corrections=3,
        )
    elif stage.id == "scheduler-core":
        stage = replace(
            stage,
            path_hints=(
                *_scheduler_context,
                "package.json",
                "tsconfig.json",
                "src/tools/fetch-batch.ts",
                "src/tools/fetch.ts",
                "src/types.ts",
            ),
            exact_paths=stage.exact_paths | frozenset({"src/tools/scheduler.ts"}),
            path_prefixes=("src/scheduler/", "src/utils/", "tests/"),
            max_corrections=3,
        )
    elif stage.id == "batch-integration":
        stage = replace(
            stage,
            path_hints=(
                "src/tools/fetch-batch.ts",
                "src/tools/fetch-batch.test.ts",
                "src/tools/scheduler.ts",
                "src/tools/fetch.ts",
                "src/types.ts",
                "package.json",
                "tsconfig.json",
            ),
            exact_paths=frozenset(
                {
                    "src/tools/fetch-batch.ts",
                    "src/tools/fetch-batch.test.ts",
                    "src/tools/scheduler.ts",
                    "src/types.ts",
                }
            ),
            path_prefixes=(),
            max_corrections=3,
        )
    elif stage.id == "challenge-hardening":
        stage = replace(
            stage,
            path_hints=(
                "src/tools/scheduler.ts",
                "tests/scheduler.test.ts",
                "src/tools/fetch-batch.ts",
                "src/tools/fetch-batch.test.ts",
                "src/types.ts",
            ),
            exact_paths=stage.exact_paths
            | frozenset(
                {
                    "src/tools/scheduler.ts",
                    "src/tools/fetch-batch.ts",
                    "src/tools/fetch-batch.test.ts",
                    "src/types.ts",
                }
            ),
            path_prefixes=("src/scheduler/", "src/utils/", "tests/"),
            max_corrections=3,
        )
    _reviewed_stages.append(stage)

codeops_staged_trial.STAGES = tuple(_reviewed_stages)


if __name__ == "__main__":
    raise SystemExit(codeops_staged_trial.main())

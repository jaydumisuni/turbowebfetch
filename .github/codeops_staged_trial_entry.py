"""Reviewed policy entry point for the live-provider CodeOps trial.

This module constrains provider context, approval scope, call pacing, and proof.
It never supplies product implementation or correction code.
"""
from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import codeops_live_provider

_original_compress = codeops_live_provider.compress_codeops_task
_original_provider_call = codeops_live_provider.GitHubModelsExecutor.__call__
_context_number = 0
_last_provider_call_at = 0.0


def _payload_from_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    task = kwargs.get("task")
    if not isinstance(task, str) and len(args) >= 2:
        task = args[1]
    if not isinstance(task, str):
        return {}
    try:
        payload = json.loads(task)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _rate_limited_provider_call(self, *args, **kwargs):
    """Space calls, retry one throttle, and reserve a bounded correction output."""
    global _last_provider_call_at
    minimum_interval = 25.0
    elapsed = time.monotonic() - _last_provider_call_at
    if _last_provider_call_at and elapsed < minimum_interval:
        time.sleep(minimum_interval - elapsed)

    is_correction = isinstance(_payload_from_call(args, kwargs).get("previous_proposal"), dict)
    previous_output_tokens = self.max_output_tokens
    if is_correction:
        self.max_output_tokens = min(previous_output_tokens, 1_600)
    try:
        try:
            result = _original_provider_call(self, *args, **kwargs)
        except codeops_live_provider.ProviderExecutionError as exc:
            if "HTTP 429" not in str(exc):
                raise
            time.sleep(65.0)
            result = _original_provider_call(self, *args, **kwargs)
    finally:
        self.max_output_tokens = previous_output_tokens
    _last_provider_call_at = time.monotonic()
    return result


codeops_live_provider.GitHubModelsExecutor.__call__ = _rate_limited_provider_call


def _previous_paths(payload: dict[str, Any]) -> tuple[str, ...]:
    previous = payload.get("previous_proposal")
    if not isinstance(previous, dict):
        return ()
    operations = previous.get("operations")
    if not isinstance(operations, list):
        return ()
    paths: list[str] = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        path = str(operation.get("path", "")).strip()
        if path and path not in paths:
            paths.append(path)
    return tuple(paths)


def _append_rules(payload: dict[str, Any], rules_to_add: tuple[str, ...]) -> None:
    rules = payload.get("rules")
    if not isinstance(rules, list):
        rules = []
        payload["rules"] = rules
    rules.extend(rules_to_add)


def _initial_stage_rules(objective: str) -> tuple[str, ...]:
    common = (
        "This repository uses NodeNext. Every relative TypeScript import, including tests, must use the runtime .js extension.",
        "Never emit a no-op replace whose old and new text are identical.",
    )
    if "proof surface" in objective:
        return common + (
            "Include src/rate-limit/limiter.test.ts in the initial proposal so correction scope cannot expand later.",
            "Use Vitest and import extractDomain from ./limiter.js.",
            "Test only confirmed behaviour: normal URL/domain lower-casing plus rejection of empty, whitespace-only, and ://: input.",
            "Use correctness-focused eslint:recommended and @typescript-eslint recommended rules only; do not add Prettier or stylistic formatting rules.",
            "Configure unused-variable rules to ignore underscore-prefixed arguments, variables, and caught errors.",
            "Preserve limiter behaviour; only remove the unnecessary slash escape identified by no-useless-escape.",
        )
    if "scheduler core" in objective:
        return common + (
            "Use generics and unknown, not explicit any.",
            "Queue timeout applies only before work starts and its timer must be cleared at start.",
            "Queued AbortSignal cancellation removes and rejects work before start; running capacity is held until the worker settles.",
            "Synchronous worker throws must become rejected task results and capacity must be released exactly once in finally.",
            "Cancellation and timeout tests must first occupy capacity with an unresolved blocker before scheduling the candidate.",
            "Validate global and per-domain limits as positive integers.",
            "Fix scheduler source/tests rather than weakening lint or proof configuration.",
        )
    if "integrate the existing scheduler core" in objective:
        return common + (
            "Modify canonical src/tools/fetch-batch.ts; do not create a parallel batch module.",
            "Include src/tools/fetch-batch.test.ts in the initial proposal.",
            "Use existing fetchPage from ./fetch.js and actual response/types from ../types.js.",
            "Do not directly acquire token-bucket or Python-process semaphores; fetchPage owns those boundaries.",
            "Use the generated scheduler from ./scheduler.js.",
            "Preserve fetchBatch, fetchMultiple, and fetchBatchWithProgress exports and option shapes.",
            "Fetch each unique URL once, preserve original output length/order, and count success/failure per unique URL.",
            "Convert an unexpected thrown worker error into a failed FetchResponse and let remaining work finish.",
            "Mock ./fetch.js in deterministic Vitest tests; never launch Chrome or Python.",
        )
    return common


def _compress_with_reviewed_policy(task: str, **kwargs):
    """Make correction requests small, exact, and independently auditable."""
    global _context_number
    payload = json.loads(task)
    correction_paths = _previous_paths(payload)
    is_correction = bool(correction_paths)

    if is_correction:
        repository = payload.get("repository")
        if not isinstance(repository, dict):
            raise codeops_live_provider.ProviderExecutionError(
                "correction request has no repository context"
            )
        files = repository.get("files")
        if not isinstance(files, list):
            raise codeops_live_provider.ProviderExecutionError(
                "correction repository files are invalid"
            )
        filtered = [
            item
            for item in files
            if isinstance(item, dict) and str(item.get("path", "")) in correction_paths
        ]
        selected_paths = {str(item.get("path", "")) for item in filtered}
        missing = [path for path in correction_paths if path not in selected_paths]
        if missing:
            raise codeops_live_provider.ProviderExecutionError(
                f"correction context is missing changed files: {missing}"
            )
        repository["files"] = filtered
        evidence = payload.get("correction_evidence")
        if isinstance(evidence, list):
            for item in evidence:
                if isinstance(item, dict) and isinstance(item.get("output"), str):
                    item["output"] = item["output"][-1_200:]
        _append_rules(
            payload,
            (
                "Correction pass: change only files from the previous proposal and only for the currently failing proof.",
                "Every supplied current file is complete and authoritative; copy replace old text exactly from it.",
                "If an exact replacement is awkward, return one complete write operation for that file. Never guess old text.",
                "Preserve all proof gates that already passed and do not redesign the stage.",
            ),
        )
        kwargs["max_chars"] = 22_000
        kwargs["max_file_chars"] = 18_000
    else:
        _append_rules(payload, _initial_stage_rules(str(payload.get("objective", "")).lower()))

    compressed, metadata = _original_compress(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        **kwargs,
    )
    _context_number += 1
    evidence_dir = Path("codeops-staged-evidence") / "provider-contexts"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / f"provider-task-{_context_number:03d}.json").write_text(
        compressed, encoding="utf-8"
    )
    (evidence_dir / f"provider-task-{_context_number:03d}-metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"
    )
    return compressed, metadata


codeops_live_provider.compress_codeops_task = _compress_with_reviewed_policy

import codeops_staged_trial

_scheduler_context = (
    "src/tools/scheduler.ts",
    "src/tools/scheduler.test.ts",
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
            path_hints=(*_scheduler_context, "package.json", "tsconfig.json", "src/tools/fetch-batch.ts", "src/tools/fetch.ts", "src/types.ts"),
            exact_paths=stage.exact_paths
            | frozenset({"src/tools/scheduler.ts", "src/tools/scheduler.test.ts"}),
            path_prefixes=("src/scheduler/", "src/utils/", "tests/"),
            max_corrections=3,
        )
    elif stage.id == "batch-integration":
        stage = replace(
            stage,
            path_hints=("src/tools/fetch-batch.ts", "src/tools/fetch-batch.test.ts", "src/tools/scheduler.ts", "src/tools/fetch.ts", "src/types.ts", "package.json", "tsconfig.json"),
            exact_paths=frozenset({"src/tools/fetch-batch.ts", "src/tools/fetch-batch.test.ts", "src/tools/scheduler.ts", "src/types.ts"}),
            path_prefixes=(),
            max_corrections=3,
        )
    elif stage.id == "challenge-hardening":
        stage = replace(
            stage,
            path_hints=("src/tools/scheduler.ts", "src/tools/scheduler.test.ts", "tests/scheduler.test.ts", "src/tools/fetch-batch.ts", "src/tools/fetch-batch.test.ts", "src/types.ts"),
            exact_paths=stage.exact_paths
            | frozenset({"src/tools/scheduler.ts", "src/tools/scheduler.test.ts", "src/tools/fetch-batch.ts", "src/tools/fetch-batch.test.ts", "src/types.ts"}),
            path_prefixes=("src/scheduler/", "src/utils/", "tests/"),
            max_corrections=3,
        )
    _reviewed_stages.append(stage)

codeops_staged_trial.STAGES = tuple(_reviewed_stages)


if __name__ == "__main__":
    raise SystemExit(codeops_staged_trial.main())

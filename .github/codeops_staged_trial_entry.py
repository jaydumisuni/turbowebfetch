"""Reviewed policy entry point for the live-provider CodeOps trial.

This module controls provider context, approval scope, call pacing, and proof. It
never contains product implementation or correction code.
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
    """Space calls, retry one throttle, and reserve correction output tokens."""
    global _last_provider_call_at
    minimum_interval = 25.0
    elapsed = time.monotonic() - _last_provider_call_at
    if _last_provider_call_at and elapsed < minimum_interval:
        time.sleep(minimum_interval - elapsed)

    is_correction = isinstance(_payload_from_call(args, kwargs).get("previous_proposal"), dict)
    previous_output_tokens = self.max_output_tokens
    if is_correction:
        self.max_output_tokens = min(previous_output_tokens, 2_400)
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


def _failed_paths(payload: dict[str, Any], previous_paths: tuple[str, ...]) -> tuple[str, ...]:
    evidence = payload.get("correction_evidence")
    text = ""
    if isinstance(evidence, list):
        text = "\n".join(
            str(item.get("output", ""))
            for item in evidence
            if isinstance(item, dict)
        )
    matched = tuple(
        path
        for path in previous_paths
        if path in text or Path(path).name in text
    )
    return matched or previous_paths


def _append_rules(payload: dict[str, Any], additions: tuple[str, ...]) -> None:
    rules = payload.get("rules")
    if not isinstance(rules, list):
        rules = []
        payload["rules"] = rules
    rules.extend(additions)


def _initial_rules(objective: str) -> tuple[str, ...]:
    common = (
        "This repository uses NodeNext; every relative TypeScript import, including tests, must use the runtime .js extension.",
        "Never emit a no-op replace whose old and new text are identical.",
    )
    if "proof surface" in objective:
        return common + (
            "Include src/rate-limit/limiter.test.ts in the initial proposal.",
            "Use Vitest and import extractDomain from ./limiter.js.",
            "Use correctness-focused eslint:recommended and @typescript-eslint recommended only; do not add Prettier or stylistic formatting rules.",
            "Ignore underscore-prefixed unused values and preserve limiter behaviour except the no-useless-escape fix.",
        )
    if "scheduler core" in objective:
        return common + (
            "Use generics and unknown, never explicit any.",
            "Queue timeout applies only before start and its timer must be cleared at start.",
            "Queued abort removes and rejects before start; running capacity remains held until the worker settles.",
            "Convert synchronous worker throws to rejection and release capacity exactly once in finally.",
            "Cancellation and timeout tests must occupy capacity with an unresolved blocker before scheduling the candidate.",
            "Validate global and per-domain limits as positive integers and fix source/tests rather than weakening proof gates.",
        )
    if "integrate the existing scheduler core" in objective:
        return common + (
            "Modify canonical src/tools/fetch-batch.ts and include src/tools/fetch-batch.test.ts initially.",
            "Use existing fetchPage from ./fetch.js, actual ../types.js contracts, and scheduler from ./scheduler.js.",
            "Do not directly acquire token-bucket or Python-process semaphores; fetchPage owns them.",
            "Preserve fetchBatch, fetchMultiple, and fetchBatchWithProgress APIs.",
            "Fetch each unique URL once, preserve original output length/order, and count results per unique URL.",
            "Convert thrown fetchPage errors to failed FetchResponse values and mock ./fetch.js in Vitest.",
        )
    return common


def _compress_with_reviewed_policy(task: str, **kwargs):
    """Use exact failed files for corrections and preserve every request as evidence."""
    global _context_number
    payload = json.loads(task)
    previous_paths = _previous_paths(payload)
    is_correction = bool(previous_paths)

    if is_correction:
        selected_paths = _failed_paths(payload, previous_paths)
        repository = payload.get("repository")
        if not isinstance(repository, dict) or not isinstance(repository.get("files"), list):
            raise codeops_live_provider.ProviderExecutionError(
                "correction request has invalid repository context"
            )
        repository["files"] = [
            item
            for item in repository["files"]
            if isinstance(item, dict) and str(item.get("path", "")) in selected_paths
        ]
        available = {str(item.get("path", "")) for item in repository["files"]}
        missing = [path for path in selected_paths if path not in available]
        if missing:
            raise codeops_live_provider.ProviderExecutionError(
                f"correction context is missing failed files: {missing}"
            )

        previous = payload.get("previous_proposal")
        if isinstance(previous, dict) and isinstance(previous.get("operations"), list):
            previous["operations"] = [
                item
                for item in previous["operations"]
                if isinstance(item, dict) and str(item.get("path", "")) in selected_paths
            ]
        evidence = payload.get("correction_evidence")
        if isinstance(evidence, list):
            for item in evidence:
                if isinstance(item, dict) and isinstance(item.get("output"), str):
                    item["output"] = item["output"][-1_400:]
        _append_rules(
            payload,
            (
                "Correction pass: edit only the failed files supplied and only for the current proof errors.",
                "Each supplied current file is complete and authoritative. Copy replace old text exactly from it.",
                "Prefer small exact replace operations. A complete write is allowed only when it fits as one valid JSON response.",
                "Preserve passed proof gates and do not redesign the stage.",
            ),
        )
        kwargs["max_chars"] = 18_000
        kwargs["max_file_chars"] = 16_000
    else:
        _append_rules(payload, _initial_rules(str(payload.get("objective", "")).lower()))

    compressed, metadata = _original_compress(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        **kwargs,
    )
    metadata["failed_file_focus"] = list(_failed_paths(payload, previous_paths)) if is_correction else []
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
        stage = replace(stage, path_hints=("src/rate-limit/limiter.test.ts", "package.json", "src/rate-limit/limiter.ts", "tsconfig.json", "src/tools/fetch-batch.ts"), max_corrections=3)
    elif stage.id == "scheduler-core":
        stage = replace(
            stage,
            path_hints=(*_scheduler_context, "package.json", "tsconfig.json", "src/tools/fetch-batch.ts", "src/tools/fetch.ts", "src/types.ts"),
            exact_paths=stage.exact_paths | frozenset({"src/tools/scheduler.ts", "src/tools/scheduler.test.ts"}),
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
            exact_paths=stage.exact_paths | frozenset({"src/tools/scheduler.ts", "src/tools/scheduler.test.ts", "src/tools/fetch-batch.ts", "src/tools/fetch-batch.test.ts", "src/types.ts"}),
            path_prefixes=("src/scheduler/", "src/utils/", "tests/"),
            max_corrections=3,
        )
    _reviewed_stages.append(stage)

codeops_staged_trial.STAGES = tuple(_reviewed_stages)


if __name__ == "__main__":
    raise SystemExit(codeops_staged_trial.main())

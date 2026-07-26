"""Entry point that applies reviewed trial policy without authoring product code."""
from __future__ import annotations

import json
from dataclasses import replace

import codeops_live_provider

_original_compress = codeops_live_provider.compress_codeops_task


def _compress_with_nodenext_rule(task: str, **kwargs):
    """Add repository-wide module and initial-scope rules to the provider contract."""
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
        rules.append(
            "The initial proposal must include src/rate-limit/limiter.test.ts with deterministic Vitest assertions for confirmed extractDomain behaviour. Do not defer this test file to a correction pass because corrections cannot expand the approved file scope."
        )
    return _original_compress(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        **kwargs,
    )


codeops_live_provider.compress_codeops_task = _compress_with_nodenext_rule

import codeops_staged_trial

_scheduler_context = (
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
            path_prefixes=("src/scheduler/", "src/utils/", "tests/"),
            max_corrections=3,
        )
    elif stage.id in {"batch-integration", "challenge-hardening"}:
        hints = tuple(dict.fromkeys((*_scheduler_context, *stage.path_hints)))
        stage = replace(
            stage,
            path_hints=hints,
            path_prefixes=("src/scheduler/", "src/utils/", "tests/"),
        )
    _reviewed_stages.append(stage)

codeops_staged_trial.STAGES = tuple(_reviewed_stages)


if __name__ == "__main__":
    raise SystemExit(codeops_staged_trial.main())

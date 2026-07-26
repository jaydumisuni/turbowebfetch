"""Entry point that applies reviewed trial policy without authoring product code."""
from __future__ import annotations

import json
from dataclasses import replace

import codeops_live_provider

_original_compress = codeops_live_provider.compress_codeops_task


def _compress_with_nodenext_rule(task: str, **kwargs):
    """Add a repository-wide module-resolution rule to the provider contract."""
    payload = json.loads(task)
    rules = payload.get("rules")
    if not isinstance(rules, list):
        rules = []
        payload["rules"] = rules
    rules.append(
        "This repository uses NodeNext. Every relative import written in TypeScript, including tests, must use the runtime .js extension."
    )
    return _original_compress(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        **kwargs,
    )


codeops_live_provider.compress_codeops_task = _compress_with_nodenext_rule

import codeops_staged_trial

_reviewed_stages = []
for stage in codeops_staged_trial.STAGES:
    if stage.id == "scheduler-core":
        stage = replace(
            stage,
            path_hints=(
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
        hints = tuple(
            dict.fromkeys(
                (
                    "src/utils/scheduler.ts",
                    "src/utils/scheduler.test.ts",
                    "tests/scheduler.test.ts",
                    "src/scheduler/",
                    *stage.path_hints,
                )
            )
        )
        stage = replace(
            stage,
            path_hints=hints,
            path_prefixes=("src/scheduler/", "src/utils/", "tests/"),
        )
    _reviewed_stages.append(stage)

codeops_staged_trial.STAGES = tuple(_reviewed_stages)


if __name__ == "__main__":
    raise SystemExit(codeops_staged_trial.main())

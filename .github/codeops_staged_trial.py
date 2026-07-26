"""Run the genuine staged CodeOps coding trial.

This file is trial orchestration only. It supplies objectives, repository context
priorities, safety scopes, approval, and proof. It does not contain the product
implementation or any correction patch.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hunter_codeops.code_ops_coding import (
    dry_run_generated_plan,
    execute_generated_plan,
    generate_coding_plan,
)
from hunter_codeops.code_ops_repository import discover_proof_commands, run_proof_commands
from hunter_codeops.code_ops_switcher import (
    ClientKind,
    ClientRoute,
    CodeOpsSwitchDecision,
    ProviderKind,
    ProviderRoute,
)
from hunter_codeops.language_mastery_assessment import assess_language_mastery
from hunter_codeops.language_mastery_models import LanguageId

from codeops_live_provider import GitHubModelsExecutor


@dataclass(frozen=True)
class Stage:
    id: str
    objective: str
    path_hints: tuple[str, ...]
    exact_paths: frozenset[str]
    path_prefixes: tuple[str, ...]
    max_corrections: int = 2


STAGES = (
    Stage(
        id="proof-gates",
        objective="""
        Repair this repository's TypeScript proof surface before feature work.
        Make the existing lint command genuinely lint the TypeScript source with a
        repository-local strict ESLint configuration, fix source violations rather than
        disabling useful rules, and make the default test script non-interactive for CI.
        Add focused deterministic tests for the existing URL-domain extraction or rate
        limiter only when needed to ensure the test command has a real assertion. Preserve
        public behaviour. Do not change the lockfile, workflows, licence, Python runtime,
        or unrelated product code. Return the strict CodeOps JSON patch only.
        """.strip(),
        path_hints=(
            "package.json",
            "src/rate-limit/limiter.ts",
            "tsconfig.json",
            "src/tools/fetch-batch.ts",
        ),
        exact_paths=frozenset(
            {
                "package.json",
                "tsconfig.json",
                ".eslintrc.cjs",
                ".eslintrc.js",
                "eslint.config.js",
                "eslint.config.mjs",
                "vitest.config.ts",
            }
        ),
        path_prefixes=("src/rate-limit/",),
        max_corrections=2,
    ),
    Stage(
        id="scheduler-core",
        objective="""
        Implement the reusable TypeScript scheduler core for this repository. It must
        accept caller-supplied asynchronous work and enforce configurable global and
        per-domain concurrency. Preserve FIFO order within each domain, rotate fairly
        across domains so a busy domain cannot starve another domain, support AbortSignal
        cancellation and a queue-wait timeout before work starts, and release capacity
        exactly once after success, cancellation, timeout, or a thrown worker. Keep the
        module independent of Chrome and the Python fetcher. Add deterministic unit tests
        for limits, FIFO, cross-domain fairness, queued cancellation, timeout, and thrown
        worker release. Do not integrate batch fetch yet and do not edit package, workflow,
        lock, licence, rate-limiter, or Python files. Return strict CodeOps JSON only.
        """.strip(),
        path_hints=(
            "src/tools/fetch-batch.ts",
            "src/tools/fetch.ts",
            "src/types.ts",
            "src/rate-limit/limiter.ts",
            "package.json",
        ),
        exact_paths=frozenset(),
        path_prefixes=("src/scheduler/",),
        max_corrections=2,
    ),
    Stage(
        id="batch-integration",
        objective="""
        Integrate the existing scheduler core into the real batch-fetch implementation.
        Replace fixed chunk barriers with dynamic scheduling while continuing to call the
        repository's real fetchPage function. Preserve the existing token-bucket rate
        limiter and the internal Python process semaphore as separate safety boundaries.
        Coalesce duplicate URLs so each unique URL is fetched once while preserving the
        original output length and order. Convert an unexpected thrown worker error into a
        failed FetchResponse for that URL so the remaining batch work finishes. Preserve
        success/failure counting semantics for unique URLs. Add deterministic tests using
        mocks or dependency injection so Chrome and Python are never launched. Do not add
        a parallel fetch implementation, edit workflows/lockfiles/licences, or simulate
        production fetching. Return strict CodeOps JSON only.
        """.strip(),
        path_hints=(
            "src/tools/fetch-batch.ts",
            "src/tools/fetch.ts",
            "src/types.ts",
            "src/scheduler/",
            "src/rate-limit/limiter.ts",
        ),
        exact_paths=frozenset(
            {
                "src/tools/fetch-batch.ts",
                "src/tools/fetch-batch.test.ts",
                "src/types.ts",
            }
        ),
        path_prefixes=("src/scheduler/",),
        max_corrections=3,
    ),
    Stage(
        id="challenge-hardening",
        objective="""
        Challenge and harden the completed fair scheduler and batch integration. Add a
        deterministic stress test with at least forty tasks across at least four domains
        that proves global and per-domain maxima, cross-domain progress, and eventual
        completion. Directly prove capacity is not leaked after a thrown worker, queued
        AbortSignal cancellation, or queue timeout. Prove duplicate result ordering and
        worker-failure isolation under overlap. Fix any implementation defect exposed by
        these tests without weakening lint, strict TypeScript, test, or build gates. Keep
        changes inside the scheduler and batch-fetch implementation/tests. Return strict
        CodeOps JSON only.
        """.strip(),
        path_hints=(
            "src/scheduler/",
            "src/tools/fetch-batch.ts",
            "src/tools/fetch-batch.test.ts",
            "src/types.ts",
        ),
        exact_paths=frozenset(
            {
                "src/tools/fetch-batch.ts",
                "src/tools/fetch-batch.test.ts",
                "src/types.ts",
            }
        ),
        path_prefixes=("src/scheduler/",),
        max_corrections=3,
    ),
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def allowed(stage: Stage, path: str) -> bool:
    if path.startswith(".github/") or path.endswith("package-lock.json"):
        return False
    if path in stage.exact_paths:
        return True
    return any(path.startswith(prefix) for prefix in stage.path_prefixes)


def route() -> CodeOpsSwitchDecision:
    provider = ProviderRoute(
        id="github-models-gpt-4.1",
        kind=ProviderKind.CUSTOM,
        model="openai/gpt-4.1",
        endpoint="https://models.github.ai/inference/chat/completions",
        local=False,
        credential_env="GITHUB_TOKEN",
        capabilities=("repo_patch",),
        notes="GitHub Models live coding trial",
    )
    return CodeOpsSwitchDecision(
        provider=provider,
        client=ClientRoute("grandmaster-staged-trial", ClientKind.TERMINAL),
        mode="repo_patch",
        review_required=True,
        reason="CodeOps owns each implementation and correction stage",
    )


def main() -> int:
    root = Path.cwd().resolve()
    evidence_root = root / "codeops-staged-evidence"
    evidence_root.mkdir(exist_ok=True)
    token = os.environ.get("GITHUB_TOKEN", "")
    decision = route()
    all_changed: set[str] = set()
    stage_packets: list[dict[str, Any]] = []
    status = "in_progress"
    error: dict[str, Any] | None = None

    try:
        for index, stage in enumerate(STAGES, 1):
            stage_dir = evidence_root / f"{index:02d}-{stage.id}"
            executor = GitHubModelsExecutor(
                token,
                stage_dir,
                path_hints=stage.path_hints,
                max_task_chars=13_000,
                max_file_chars=5_000,
                max_output_tokens=4_000,
            )
            packet: dict[str, Any] = {
                "stage": stage.id,
                "objective_sha256": sha256_text(stage.objective),
                "path_hints": list(stage.path_hints),
                "approved_exact_paths": sorted(stage.exact_paths),
                "approved_prefixes": list(stage.path_prefixes),
            }
            try:
                plan = generate_coding_plan(
                    decision,
                    stage.objective,
                    root,
                    operation_id=f"grandmaster-live-{index}-{stage.id}",
                    executor=executor,
                    timeout=300.0,
                )
                blocked = [
                    operation.path
                    for operation in plan.proposal.operations
                    if not allowed(stage, operation.path)
                ]
                if blocked:
                    raise RuntimeError(
                        f"stage {stage.id} generated changes outside approved scope: {blocked}"
                    )
                dry_run = dry_run_generated_plan(root, plan)
                execution = execute_generated_plan(
                    decision,
                    plan,
                    approved=True,
                    max_corrections=stage.max_corrections,
                    executor=executor,
                )
                packet.update(
                    {
                        "provider_calls": executor.calls,
                        "plan": plan.to_dict(),
                        "dry_run": dry_run,
                        "execution": execution.to_dict(),
                    }
                )
                if execution.status != "ready_for_sergeant_review" or execution.restored:
                    raise RuntimeError(
                        f"stage {stage.id} did not complete: {execution.status}, "
                        f"restored={execution.restored}"
                    )
                all_changed.update(execution.changed_files)
            except Exception as exc:
                packet.update(
                    {
                        "provider_calls": executor.calls,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    }
                )
                stage_packets.append(packet)
                (stage_dir / "stage-evidence.json").write_text(
                    json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8"
                )
                raise
            packet["status"] = "passed"
            stage_packets.append(packet)
            (stage_dir / "stage-evidence.json").write_text(
                json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8"
            )

        changed = tuple(sorted(all_changed))
        if not changed:
            raise RuntimeError("CodeOps completed without any product changes")
        final_commands = discover_proof_commands(root, changed)
        final_results = run_proof_commands(root, final_commands)
        if not final_commands or len(final_results) != len(final_commands):
            raise RuntimeError("final repository proof profile did not complete")
        if not all(result.passed for result in final_results):
            raise RuntimeError("final repository proof failed")

        diff = subprocess.run(
            ["git", "diff", "--binary", "--", *changed],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        if not diff.strip():
            raise RuntimeError("CodeOps product diff is empty")
        (root / "codeops-live-generated.patch").write_text(diff, encoding="utf-8")
        (root / "codeops-live-changed-files.txt").write_text(
            "\n".join(changed) + "\n", encoding="utf-8"
        )
        assessment = assess_language_mastery(
            LanguageId.TYPESCRIPT,
            root,
            (),
            changed_files=changed,
        )
        status = "ready_for_sergeant_review"
        final_packet = {
            "classification": "codeops_live_provider_staged_authored_trial",
            "status": status,
            "repository": os.environ.get("GITHUB_REPOSITORY"),
            "trial_head_sha": os.environ.get("TRIAL_HEAD_SHA"),
            "workflow_run_id": os.environ.get("TRIAL_RUN_ID"),
            "provider": {
                "id": decision.provider.id,
                "model": decision.provider.model,
                "endpoint": decision.provider.endpoint,
                "live_provider": True,
            },
            "stages": stage_packets,
            "changed_files": list(changed),
            "final_commands": [command.to_dict() for command in final_commands],
            "final_results": [result.to_dict() for result in final_results],
            "generated_diff_sha256": sha256_text(diff),
            "assessment": assessment.to_dict(),
            "human_authored_production_code": False,
            "grand_master_claim": False,
        }
    except Exception as exc:
        status = "failed"
        error = {
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        final_packet = {
            "classification": "codeops_live_provider_staged_authored_trial",
            "status": status,
            "repository": os.environ.get("GITHUB_REPOSITORY"),
            "trial_head_sha": os.environ.get("TRIAL_HEAD_SHA"),
            "workflow_run_id": os.environ.get("TRIAL_RUN_ID"),
            "provider": {
                "id": decision.provider.id,
                "model": decision.provider.model,
                "endpoint": decision.provider.endpoint,
                "live_provider": True,
            },
            "stages": stage_packets,
            "error": error,
            "human_authored_production_code": False,
            "grand_master_claim": False,
        }

    (root / "codeops-live-trial-evidence.json").write_text(
        json.dumps(final_packet, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(final_packet, indent=2, sort_keys=True))
    return 0 if status == "ready_for_sergeant_review" else 1


if __name__ == "__main__":
    raise SystemExit(main())

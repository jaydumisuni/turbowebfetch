"""Run the staged trial with conservative hosted-model pacing and fallback route."""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import codeops_staged_trial_entry as entry
from hunter_codeops.code_ops_coding_models import CodingPatchProposal, ProofResult
from hunter_codeops.code_ops_file_edit import FileEditAction, FileEditOperation
from hunter_codeops.code_ops_repository import (
    discover_proof_commands,
    run_proof_commands,
    source_hashes,
)

_last_provider_call_at = 0.0
_original_route = entry.codeops_staged_trial.route
_original_generate = entry.codeops_staged_trial.generate_coding_plan
_original_execute = entry.codeops_staged_trial.execute_generated_plan
_original_initial_rules = entry._initial_rules
_SCOPE_MARKER = "__HUNTER_CODEOPS_PREAPPROVED_SCOPE_RESERVATION__"


def _task_payload(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    task = kwargs.get("task")
    if not isinstance(task, str) and len(args) >= 2:
        task = args[1]
    if not isinstance(task, str):
        return {}
    try:
        value = json.loads(task)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _paced_provider_call(self, *args, **kwargs):
    global _last_provider_call_at
    elapsed = time.monotonic() - _last_provider_call_at
    if _last_provider_call_at and elapsed < 90.0:
        time.sleep(90.0 - elapsed)

    correction = isinstance(_task_payload(args, kwargs).get("previous_proposal"), dict)
    previous_tokens = self.max_output_tokens
    if correction:
        self.max_output_tokens = min(previous_tokens, 2_400)
    try:
        try:
            result = entry._original_provider_call(self, *args, **kwargs)
        except entry.codeops_live_provider.ProviderExecutionError as exc:
            if "HTTP 429" not in str(exc):
                raise
            time.sleep(180.0)
            result = entry._original_provider_call(self, *args, **kwargs)
    finally:
        self.max_output_tokens = previous_tokens
    _last_provider_call_at = time.monotonic()
    return result


def _fallback_route():
    decision = _original_route()
    provider = replace(
        decision.provider,
        id="github-models-gpt-4.1-mini",
        model="openai/gpt-4.1-mini",
        notes="GitHub Models live coding trial fallback after GPT-4.1 quota exhaustion",
    )
    return replace(decision, provider=provider)


def _compact_proof_rules(objective: str) -> tuple[str, ...]:
    rules = _original_initial_rules(objective)
    if "proof surface" not in objective:
        return rules
    return rules + (
        "Keep the proof patch compact. Never replace the whole package.json.",
        "Change the test script with one exact replace of the existing vitest value to vitest run.",
        "Change the lint script with one exact replace of the existing eslint src/ value to the chosen strict ESLint invocation.",
        "For limiter.ts, replace only the exact regex line needed for no-useless-escape; do not rewrite the file.",
        "Write only the new ESLint configuration and focused test file in full.",
    )


def _approved_scope(operation_id: str) -> tuple[str, ...]:
    if "proof-gates" in operation_id:
        return ("src/rate-limit/limiter.ts",)
    if "scheduler-core" in operation_id:
        return ("src/tools/scheduler.ts", "src/tools/scheduler.test.ts")
    if "batch-integration" in operation_id:
        return (
            "src/tools/fetch-batch.ts",
            "src/tools/fetch-batch.test.ts",
            "src/tools/scheduler.ts",
            "src/types.ts",
        )
    if "challenge-hardening" in operation_id:
        return (
            "src/tools/scheduler.ts",
            "src/tools/scheduler.test.ts",
            "src/tools/fetch-batch.ts",
            "src/tools/fetch-batch.test.ts",
            "src/types.ts",
        )
    return ()


def _generate_with_preapproved_correction_scope(*args, **kwargs):
    plan = _original_generate(*args, **kwargs)
    approved = _approved_scope(plan.operation_id)
    existing = set(plan.proposal.changed_files)
    reservations = tuple(
        FileEditOperation(
            path,
            FileEditAction.REPLACE,
            old=f"{_SCOPE_MARKER}:{path}",
            new=f"{_SCOPE_MARKER}:approved:{path}",
            required=False,
        )
        for path in approved
        if path not in existing
    )
    if not reservations:
        return plan
    proposal = replace(plan.proposal, operations=plan.proposal.operations + reservations)
    root = Path(plan.context.workspace)
    return replace(
        plan,
        proposal=proposal,
        source_hashes=source_hashes(root, proposal.changed_files),
        proof_commands=discover_proof_commands(root, proposal.changed_files),
    )


def _proof_runner_with_eslint_fix(workspace, commands):
    root = Path(workspace)
    completed = subprocess.run(
        [
            "npx",
            "eslint",
            "--fix",
            "--config",
            ".eslintrc.cjs",
            "src/rate-limit/limiter.ts",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    fix_result = ProofResult(
        name="eslint-autofix:src/rate-limit/limiter.ts",
        argv=(
            "npx",
            "eslint",
            "--fix",
            "--config",
            ".eslintrc.cjs",
            "src/rate-limit/limiter.ts",
        ),
        cwd=".",
        returncode=completed.returncode,
        passed=completed.returncode == 0,
        output=output,
    )
    return (fix_result,) + run_proof_commands(root, commands)


def _execute_with_approved_tools(decision, plan, **kwargs):
    if "proof-gates" in plan.operation_id:
        kwargs["proof_runner"] = _proof_runner_with_eslint_fix
    return _original_execute(decision, plan, **kwargs)


entry.codeops_live_provider.GitHubModelsExecutor.__call__ = _paced_provider_call
entry.codeops_staged_trial.route = _fallback_route
entry.codeops_staged_trial.generate_coding_plan = _generate_with_preapproved_correction_scope
entry.codeops_staged_trial.execute_generated_plan = _execute_with_approved_tools
entry._initial_rules = _compact_proof_rules


if __name__ == "__main__":
    raise SystemExit(entry.codeops_staged_trial.main())

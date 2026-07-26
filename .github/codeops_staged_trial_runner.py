"""Run the staged trial with conservative hosted-model pacing and fallback route."""
from __future__ import annotations

import json
import time
from dataclasses import replace
from typing import Any

import codeops_staged_trial_entry as entry

_last_provider_call_at = 0.0
_original_route = entry.codeops_staged_trial.route
_original_initial_rules = entry._initial_rules


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
    """Use a separately rate-limited GitHub Models coding route."""
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


entry.codeops_live_provider.GitHubModelsExecutor.__call__ = _paced_provider_call
entry.codeops_staged_trial.route = _fallback_route
entry._initial_rules = _compact_proof_rules


if __name__ == "__main__":
    raise SystemExit(entry.codeops_staged_trial.main())

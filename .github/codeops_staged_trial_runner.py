"""Run the staged trial with conservative hosted-model pacing."""
from __future__ import annotations

import json
import time
from typing import Any

import codeops_staged_trial_entry as entry

_last_provider_call_at = 0.0


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


entry.codeops_live_provider.GitHubModelsExecutor.__call__ = _paced_provider_call


if __name__ == "__main__":
    raise SystemExit(entry.codeops_staged_trial.main())

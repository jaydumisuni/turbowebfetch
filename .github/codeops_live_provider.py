"""Bounded GitHub Models executor used only by the live CodeOps coding trial."""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from hunter_codeops.code_ops_provider import ProviderExecutionError, ProviderExecutionResult
from hunter_codeops.code_ops_switcher import CodeOpsSwitchDecision


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compress_codeops_task(
    task: str,
    *,
    max_chars: int = 10_000,
    max_file_chars: int = 3_500,
) -> tuple[str, dict[str, Any]]:
    """Reduce provider context while keeping CodeOps' full local recovery intact."""
    try:
        payload = json.loads(task)
    except json.JSONDecodeError as exc:
        raise ProviderExecutionError("CodeOps provider task is not JSON") from exc
    if not isinstance(payload, dict):
        raise ProviderExecutionError("CodeOps provider task root is not an object")
    repository = payload.get("repository")
    if not isinstance(repository, dict):
        raise ProviderExecutionError("CodeOps provider task has no repository object")
    raw_files = repository.get("files", [])
    if not isinstance(raw_files, list):
        raise ProviderExecutionError("CodeOps provider repository files are invalid")

    repository["files"] = []
    base = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    remaining = max_chars - len(base)
    if remaining < 512:
        raise ProviderExecutionError("CodeOps task metadata exceeds the provider context budget")
    selected: list[dict[str, Any]] = []
    selected_paths: list[str] = []
    for item in raw_files:
        if not isinstance(item, dict) or remaining < 512:
            continue
        path = str(item.get("path", ""))
        content = item.get("content", "")
        if not path or not isinstance(content, str):
            continue
        content = content[: min(max_file_chars, max(0, remaining - 256))]
        candidate = dict(item)
        candidate["content"] = content
        encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > remaining:
            continue
        selected.append(candidate)
        selected_paths.append(path)
        remaining -= len(encoded) + 1

    repository["files"] = selected
    repository["provider_context_truncated"] = len(selected) < len(raw_files)
    compressed = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(compressed) > max_chars:
        raise ProviderExecutionError("bounded provider task still exceeds its character budget")
    metadata = {
        "original_task_chars": len(task),
        "original_task_sha256": _sha256(task),
        "provider_task_chars": len(compressed),
        "provider_task_sha256": _sha256(compressed),
        "original_file_count": len(raw_files),
        "provider_file_count": len(selected),
        "provider_paths": selected_paths,
        "max_chars": max_chars,
        "max_file_chars": max_file_chars,
    }
    return compressed, metadata


class GitHubModelsExecutor:
    def __init__(
        self,
        token: str,
        output_directory: str | Path,
        *,
        model: str = "openai/gpt-4.1",
        endpoint: str = "https://models.github.ai/inference/chat/completions",
        max_output_tokens: int = 4_000,
    ) -> None:
        if not token:
            raise ProviderExecutionError("GitHub Models token is unavailable")
        self.token = token
        self.model = model
        self.endpoint = endpoint
        self.max_output_tokens = max_output_tokens
        self.output_directory = Path(output_directory)
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        selected: CodeOpsSwitchDecision,
        task: str,
        *,
        system_prompt: str,
        timeout: float = 300.0,
    ) -> ProviderExecutionResult:
        number = len(self.calls) + 1
        provider_task, context_metadata = compress_codeops_task(task)
        body = {
            "model": selected.provider.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": provider_task},
            ],
            "temperature": 0.1,
            "max_tokens": self.max_output_tokens,
        }
        request_text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        request = urllib.request.Request(
            selected.provider.endpoint,
            data=request_text.encode("utf-8"),
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2026-03-10",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            (self.output_directory / f"provider-http-error-{number}.txt").write_text(
                detail, encoding="utf-8"
            )
            raise ProviderExecutionError(
                f"GitHub Models call {number} failed with HTTP {exc.code}: {detail[-4000:]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ProviderExecutionError(f"GitHub Models call {number} failed: {exc}") from exc

        try:
            payload = json.loads(response_text)
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderExecutionError(
                f"GitHub Models call {number} returned an unsupported response"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderExecutionError(f"GitHub Models call {number} returned empty content")

        (self.output_directory / f"provider-output-{number}.txt").write_text(
            content, encoding="utf-8"
        )
        call = {
            "call": number,
            "model": selected.provider.model,
            "request_sha256": _sha256(request_text),
            "response_sha256": _sha256(response_text),
            "output_sha256": _sha256(content),
            "output_chars": len(content),
            "finish_reason": payload.get("choices", [{}])[0].get("finish_reason"),
            "usage": payload.get("usage", {}),
            "context": context_metadata,
            "max_output_tokens": self.max_output_tokens,
        }
        self.calls.append(call)
        return ProviderExecutionResult(
            provider_id=selected.provider.id,
            model=selected.provider.model,
            output_text=content,
            raw={
                "provider": "github-models",
                "call": number,
                "response_sha256": call["response_sha256"],
            },
        )

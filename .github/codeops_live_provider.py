"""Bounded GitHub Models executor used only by the live CodeOps coding trial."""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Sequence

from hunter_codeops.code_ops_provider import ProviderExecutionError, ProviderExecutionResult
from hunter_codeops.code_ops_switcher import CodeOpsSwitchDecision

from codeops_patch_normalizer import normalize_provider_patch


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _text_summary(value: Any) -> dict[str, Any]:
    text = value if isinstance(value, str) else ""
    return {"chars": len(text), "sha256": _sha256(text), "present": bool(text)}


def _compact_correction_history(payload: dict[str, Any]) -> None:
    previous = payload.get("previous_proposal")
    if isinstance(previous, dict):
        operations = previous.get("operations")
        if isinstance(operations, list):
            compact_operations: list[dict[str, Any]] = []
            for item in operations:
                if not isinstance(item, dict):
                    continue
                compact_operations.append(
                    {
                        "path": item.get("path"),
                        "action": item.get("action"),
                        "required": item.get("required", True),
                        "content": _text_summary(item.get("content")),
                        "old": _text_summary(item.get("old")),
                        "new": _text_summary(item.get("new")),
                    }
                )
            previous["operations"] = compact_operations
            previous["operation_text_omitted"] = True
    evidence = payload.get("correction_evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, dict) and isinstance(item.get("output"), str):
                output = item["output"]
                item["output"] = output[-2400:]
                item["output_original_chars"] = len(output)
                item["output_sha256"] = _sha256(output)


def compress_codeops_task(
    task: str,
    *,
    path_hints: Sequence[str] = (),
    max_chars: int = 13_000,
    max_file_chars: int = 5_000,
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

    is_correction = isinstance(payload.get("previous_proposal"), dict)
    _compact_correction_history(payload)

    rules = payload.get("rules")
    if not isinstance(rules, list):
        rules = []
        payload["rules"] = rules
    rules.extend(
        (
            "Use at most one operation for any path. If a file needs multiple edits, emit one write operation containing the complete final file or one sufficiently specific replace operation.",
            "For an ESLint configuration in this repository, use .eslintrc.cjs rather than .eslintrc.json.",
            "Do not invent parallel modules when an existing repository interface satisfies the objective; modify or import the recovered canonical files.",
        )
    )
    if is_correction:
        rules.extend(
            (
                "This is a correction pass. Fix only the currently failing proof evidence; do not restate or redesign the original solution.",
                "Preserve every proof gate that already passed. Do not modify its configuration or source unless the current failure explicitly names that file as the cause.",
                "Use the smallest possible operation set, usually one exact replace in the file named by the failure.",
                "When a lint failure names an intentionally unused underscore-prefixed variable outside the approved source scope, correct the no-unused-vars configuration instead of requesting or editing that source file.",
            )
        )
    objective = str(payload.get("objective", "")).lower()
    if "proof surface" in objective:
        rules.extend(
            (
                "Use only installed ESLint configurations and plugins. Do not extend prettier or any package absent from package.json.",
                "Use correctness-focused eslint:recommended and @typescript-eslint recommended rules compatible with the existing source. Do not enable type-aware strict, stylistic, quote, indent, comma, maximum-line-length, or formatting rules that create repository-wide churn.",
                "Do not manually enable rules requiring parserServices or parserOptions.project, including no-floating-promises, no-misused-promises, no-unsafe-* rules, await-thenable, restrict-plus-operands, restrict-template-expressions, or unbound-method.",
                "Configure @typescript-eslint/no-unused-vars to ignore underscore-prefixed arguments, variables, and caught errors using argsIgnorePattern, varsIgnorePattern, and caughtErrorsIgnorePattern set to ^_.",
                "Keep Vitest as the test framework and make the existing test script run Vitest non-interactively; do not replace it with Node's test runner.",
                "All relative TypeScript test imports must use the explicit .js extension required by this NodeNext repository.",
                "Proof tests must assert confirmed existing public behaviour. Do not introduce an assertion that requires changing public behaviour merely to satisfy the test. In particular, do not assert that the string 'not a url' throws because the existing schemeless fallback accepts a leading hostname token.",
                "For src/rate-limit/limiter.ts, preserve every existing export, class method, configuration, and behaviour. The only expected source correction is a minimal exact replacement removing the unnecessary slash escape identified by no-useless-escape; do not alter token-bucket logic or write a replacement copy of the file.",
                "Include that minimal regex correction in the initial proposal so later corrections do not require new file scope.",
            )
        )

    hint_order = {path: index for index, path in enumerate(path_hints)}

    def rank(item: Any) -> tuple[int, int, str]:
        if not isinstance(item, dict):
            return (99, 99, "")
        path = str(item.get("path", ""))
        for hint, index in hint_order.items():
            if path == hint or path.endswith(hint) or hint in path:
                return (0, index, path)
        if path.startswith("src/") and ("test" in path.lower() or path.endswith(".spec.ts")):
            return (1, 0, path)
        if path.startswith("src/"):
            return (2, 0, path)
        if path in {"package.json", "tsconfig.json", ".eslintrc.cjs", "eslint.config.js"}:
            return (3, 0, path)
        if path.endswith(("README.md", "AGENTS.md", "CONTRIBUTING.md")):
            return (4, 0, path)
        return (5, 0, path)

    ordered_files = sorted(raw_files, key=rank)
    repository["files"] = []
    repository["provider_context_truncated"] = bool(raw_files)
    base = _encode(payload)
    remaining = max_chars - len(base)
    if remaining < 512:
        raise ProviderExecutionError("CodeOps task metadata exceeds the provider context budget")

    selected: list[dict[str, Any]] = []
    selected_paths: list[str] = []
    for item in ordered_files:
        if not isinstance(item, dict) or remaining < 512:
            continue
        path = str(item.get("path", ""))
        content = item.get("content", "")
        if not path or not isinstance(content, str):
            continue
        content = content[: min(max_file_chars, max(0, remaining - 384))]
        candidate = dict(item)
        candidate["content"] = content
        encoded = _encode(candidate)
        if len(encoded) + 1 > remaining:
            continue
        selected.append(candidate)
        selected_paths.append(path)
        remaining -= len(encoded) + 1

    repository["files"] = selected
    repository["provider_context_truncated"] = len(selected) < len(raw_files)
    compressed = _encode(payload)
    while selected and len(compressed) > max_chars:
        selected.pop()
        selected_paths.pop()
        repository["files"] = selected
        repository["provider_context_truncated"] = True
        compressed = _encode(payload)
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
        "path_hints": list(path_hints),
        "max_chars": max_chars,
        "max_file_chars": max_file_chars,
        "correction_history_compacted": is_correction,
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
        path_hints: Sequence[str] = (),
        max_task_chars: int = 13_000,
        max_file_chars: int = 5_000,
        max_output_tokens: int = 4_000,
    ) -> None:
        if not token:
            raise ProviderExecutionError("GitHub Models token is unavailable")
        self.token = token
        self.model = model
        self.endpoint = endpoint
        self.path_hints = tuple(path_hints)
        self.max_task_chars = max_task_chars
        self.max_file_chars = max_file_chars
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
        provider_task, context_metadata = compress_codeops_task(
            task,
            path_hints=self.path_hints,
            max_chars=self.max_task_chars,
            max_file_chars=self.max_file_chars,
        )
        body = {
            "model": selected.provider.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": provider_task},
            ],
            "temperature": 0.1,
            "max_tokens": self.max_output_tokens,
        }
        request_text = _encode(body)
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
            raw_content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderExecutionError(
                f"GitHub Models call {number} returned an unsupported response"
            ) from exc
        if not isinstance(raw_content, str) or not raw_content.strip():
            raise ProviderExecutionError(f"GitHub Models call {number} returned empty content")

        (self.output_directory / f"provider-output-{number}.txt").write_text(
            raw_content, encoding="utf-8"
        )
        normalized_content, normalizations = normalize_provider_patch(raw_content, Path.cwd())
        if normalized_content != raw_content:
            (self.output_directory / f"provider-output-{number}-normalized.txt").write_text(
                normalized_content, encoding="utf-8"
            )

        call = {
            "call": number,
            "model": selected.provider.model,
            "request_sha256": _sha256(request_text),
            "response_sha256": _sha256(response_text),
            "raw_output_sha256": _sha256(raw_content),
            "output_sha256": _sha256(normalized_content),
            "raw_output_chars": len(raw_content),
            "output_chars": len(normalized_content),
            "finish_reason": payload.get("choices", [{}])[0].get("finish_reason"),
            "usage": payload.get("usage", {}),
            "context": context_metadata,
            "max_output_tokens": self.max_output_tokens,
            "normalizations": normalizations,
        }
        self.calls.append(call)
        return ProviderExecutionResult(
            provider_id=selected.provider.id,
            model=selected.provider.model,
            output_text=normalized_content,
            raw={
                "provider": "github-models",
                "call": number,
                "response_sha256": call["response_sha256"],
                "normalizations": normalizations,
            },
        )

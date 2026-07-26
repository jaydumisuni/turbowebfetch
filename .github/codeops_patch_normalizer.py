"""Normalise only mechanically equivalent provider patch operations.

The normaliser never authors code. It may consolidate multiple exact replace
operations for one existing file into one write operation because CodeOps
requires one operation per path. Mixed actions, missing source text, repeated
source text, and new files fail closed.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from hunter_codeops.code_ops_provider import ProviderExecutionError


def normalize_provider_patch(
    text: str,
    workspace: str | Path,
) -> tuple[str, list[dict[str, Any]]]:
    clean = text.strip()
    fenced = clean.startswith("```")
    if fenced:
        first_newline = clean.find("\n")
        if first_newline < 0 or not clean.endswith("```"):
            return text, []
        clean = clean[first_newline + 1 : -3].strip()
    try:
        payload = json.loads(clean)
    except json.JSONDecodeError:
        return text, []
    if not isinstance(payload, dict):
        return text, []
    operations = payload.get("operations")
    if not isinstance(operations, list) or not all(isinstance(item, dict) for item in operations):
        return text, []

    paths = [str(item.get("path", "")) for item in operations]
    duplicates = {path for path, count in Counter(paths).items() if path and count > 1}
    if not duplicates:
        return text, []

    root = Path(workspace).resolve()
    replacements: dict[str, dict[str, Any]] = {}
    normalizations: list[dict[str, Any]] = []
    for path in duplicates:
        group = [item for item in operations if item.get("path") == path]
        if any(item.get("action") != "replace" for item in group):
            raise ProviderExecutionError(
                f"duplicate provider path uses mixed or non-replace actions: {path}"
            )
        target = (root / path).resolve()
        if root != target and root not in target.parents:
            raise ProviderExecutionError(f"duplicate provider path escapes workspace: {path}")
        if not target.is_file():
            raise ProviderExecutionError(f"duplicate provider path is not an existing file: {path}")
        content = target.read_text(encoding="utf-8")
        for item in group:
            old = item.get("old")
            new = item.get("new")
            if not isinstance(old, str) or not old or not isinstance(new, str) or old == new:
                raise ProviderExecutionError(f"duplicate replacement is invalid: {path}")
            if content.count(old) != 1:
                raise ProviderExecutionError(
                    f"duplicate replacement source is not exact and unique: {path}"
                )
            content = content.replace(old, new, 1)
        replacements[path] = {
            "path": path,
            "action": "write",
            "content": content,
            "old": "",
            "new": "",
            "required": all(item.get("required", True) is True for item in group),
        }
        normalizations.append(
            {
                "path": path,
                "source_operations": len(group),
                "result_action": "write",
                "mechanical_only": True,
            }
        )

    emitted: set[str] = set()
    normalized_operations: list[dict[str, Any]] = []
    for item in operations:
        path = str(item.get("path", ""))
        if path in duplicates:
            if path not in emitted:
                normalized_operations.append(replacements[path])
                emitted.add(path)
        else:
            normalized_operations.append(item)
    payload["operations"] = normalized_operations
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")), normalizations

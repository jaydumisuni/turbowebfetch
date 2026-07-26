"""Trial-only runtime hardening for the installed Hunter CodeOps package.

Python imports sitecustomize from the script directory before the trial entry point.
This moves transactional file backups outside the repository so repository-native
test discovery cannot execute copied test files. Product source is untouched.
"""
from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

import hunter_codeops.code_ops_file_edit as file_edit


def _external_backup_dir(root: Path) -> Path:
    identity = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    base = Path(tempfile.gettempdir()) / "hunter-codeops-backups" / identity
    candidate = base / "latest"
    if candidate.exists():
        shutil.rmtree(candidate)
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


file_edit._make_backup_dir = _external_backup_dir

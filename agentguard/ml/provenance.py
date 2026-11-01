"""Experiment provenance and artifact sidecar helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


RELEVANT_SOURCE_PATHS: tuple[str, ...] = (
    "agentguard/ml/__init__.py",
    "agentguard/ml/analysis.py",
    "agentguard/ml/config.py",
    "agentguard/ml/data.py",
    "agentguard/ml/evaluation.py",
    "agentguard/ml/explainability.py",
    "agentguard/ml/models.py",
    "agentguard/ml/preprocessing.py",
    "agentguard/ml/provenance.py",
    "agentguard/ml/splitting.py",
    "agentguard/ml/thresholds.py",
    "agentguard/ml/train.py",
    "agentguard/ml/training.py",
    "agentguard/processing/features.py",
    "agentguard/dataset/generator.py",
    "agentguard/dataset/labels.py",
    "pyproject.toml",
)


def git_head() -> str | None:
    """Return the current base commit, not a substitute for uncommitted hashes."""
    return _git_output("rev-parse", "HEAD")


def git_branch() -> str | None:
    """Return the current Git branch name when available."""
    return _git_output("rev-parse", "--abbrev-ref", "HEAD")


def worktree_dirty() -> bool | None:
    """Return whether the worktree contains staged, unstaged, or untracked changes."""
    output = _git_output("status", "--porcelain")
    return bool(output.strip()) if output is not None else None


def source_file_hashes() -> dict[str, str]:
    """Return deterministic SHA-256 hashes for relevant source files."""
    root = _repo_root()
    hashes: dict[str, str] = {}
    for relative in RELEVANT_SOURCE_PATHS:
        path = root / relative
        if path.is_file():
            hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def code_snapshot_sha256(hashes: dict[str, str] | None = None) -> str:
    """Hash the relevant source-file hash map, including uncommitted changes."""
    payload = hashes if hashes is not None else source_file_hashes()
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def artifact_sha256(path: str | Path) -> str:
    """Return the SHA-256 of an existing artifact file."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_artifact_sidecar(path: str | Path) -> Path:
    """Write a sidecar manifest containing the final artifact SHA-256.

    The digest is intentionally stored beside the artifact rather than embedded
    inside it, avoiding a self-referential artifact hash.
    """
    artifact = Path(path)
    digest = artifact_sha256(artifact)
    sidecar = artifact.with_name(f"{artifact.stem}.sha256.json")
    payload: dict[str, Any] = {
        "artifact": artifact.as_posix(),
        "artifact_sha256": digest,
        "artifact_size_bytes": int(artifact.stat().st_size),
        "hash_algorithm": "sha256",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "note": "Sidecar digest covers the final artifact bytes; it is not embedded in the artifact.",
    }
    sidecar.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return sidecar


def _git_output(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], check=True, capture_output=True, text=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

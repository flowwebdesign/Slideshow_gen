from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from pathlib import Path


JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{22}$")


def generate_job_id() -> str:
    return secrets.token_urlsafe(16)


def generate_access_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token: str, digest: str) -> bool:
    return hmac.compare_digest(token_digest(token), digest)


def safe_job_path(jobs_root: Path, job_id: str) -> Path:
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise ValueError("invalid job id")
    root = jobs_root.resolve()
    candidate = (root / job_id).resolve()
    if candidate.parent != root:
        raise ValueError("job path escapes root")
    return candidate


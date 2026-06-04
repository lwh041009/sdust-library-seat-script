"""
Shared helpers for finding the library JWT in Reqable capture files.
"""

import base64
import gzip
import json
import os
import re
import time
import zlib
from datetime import datetime
from pathlib import Path


JWT_RE = re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+")
TOKEN_FIELD_RE = re.compile(
    r'"(?:token|Token|access_token|accessToken|id_token)"\s*:\s*"([^"]{20,})"'
)
BEARER_RE = re.compile(r"Bearer\s+([A-Za-z0-9._~+/=-]{20,})", re.IGNORECASE)
HEADER_TOKEN_RE = re.compile(r"(?im)^\s*Token\s*:\s*([A-Za-z0-9._~+/=-]{20,})\s*$")


def find_reqable_capture_dir():
    """Find Reqable's capture directory on Windows."""
    candidates = []

    appdata = os.environ.get("APPDATA", "")
    if appdata:
        candidates.append(Path(appdata) / "Reqable" / "capture")

    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        candidates.append(Path(localappdata) / "Reqable" / "capture")

    userprofile = os.environ.get("USERPROFILE", "")
    if userprofile:
        base = Path(userprofile)
        candidates.extend(
            [
                base / "AppData" / "Roaming" / "Reqable" / "capture",
                base / "AppData" / "Local" / "Reqable" / "capture",
            ]
        )

    seen = set()
    for path in candidates:
        resolved = str(path)
        if resolved in seen:
            continue
        seen.add(resolved)
        if path.is_dir():
            return str(path)

    for drive in ("C:", "D:", "E:"):
        users_dir = Path(drive + os.sep) / "Users"
        if not users_dir.is_dir():
            continue
        try:
            users = list(users_dir.iterdir())
        except OSError:
            continue
        for user_dir in users:
            for sub in (
                Path("AppData") / "Roaming" / "Reqable" / "capture",
                Path("AppData") / "Local" / "Reqable" / "capture",
            ):
                path = user_dir / sub
                if path.is_dir():
                    return str(path)

    return None


def decode_body(data):
    """Decode a Reqable saved body as text."""
    if not data:
        return ""

    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass

    for decoder in (gzip.decompress, zlib.decompress):
        try:
            raw = decoder(data)
        except Exception:
            continue
        for encoding in ("utf-8", "utf-8-sig", "gb18030"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                pass

    try:
        import brotli

        raw = brotli.decompress(data)
        return raw.decode("utf-8", errors="ignore")
    except Exception:
        pass

    return data.decode("utf-8", errors="ignore")


def decode_jwt_payload(token):
    """Decode a JWT payload without verifying its signature."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return None


def jwt_exp(payload):
    exp = payload.get("exp") or payload.get("expiration") or payload.get("expire") or 0
    if not isinstance(exp, (int, float)):
        return 0
    if exp > 100000000000:
        exp = exp // 1000
    return int(exp)


def library_user_id(payload):
    """The library token uses camel-case userId."""
    user_id = payload.get("userId") or payload.get("UserId") or payload.get("userid")
    if user_id is None:
        return ""
    return str(user_id)


def format_exp(exp):
    if not exp:
        return "unknown"
    return datetime.fromtimestamp(exp).strftime("%Y-%m-%d %H:%M:%S")


def is_library_token_valid(token, expected_user_id=None, min_seconds=300):
    if not token or len(token) < 20:
        return False
    payload = decode_jwt_payload(token)
    if not payload:
        return False
    user_id = library_user_id(payload)
    if not user_id:
        return False
    if expected_user_id and user_id != str(expected_user_id):
        return False
    exp = jwt_exp(payload)
    return bool(exp and time.time() < exp - min_seconds)


def _walk_json_tokens(value, out):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"token", "Token", "access_token", "accessToken", "id_token"}:
                if isinstance(child, str) and len(child) > 20:
                    out.append(child)
            _walk_json_tokens(child, out)
    elif isinstance(value, list):
        for child in value:
            _walk_json_tokens(child, out)


def extract_token_candidates(text):
    candidates = []

    try:
        obj = json.loads(text)
        _walk_json_tokens(obj, candidates)
    except Exception:
        pass

    candidates.extend(match.group(1) for match in TOKEN_FIELD_RE.finditer(text))
    candidates.extend(match.group(1) for match in BEARER_RE.finditer(text))
    candidates.extend(match.group(1) for match in HEADER_TOKEN_RE.finditer(text))
    candidates.extend(match.group(0) for match in JWT_RE.finditer(text))

    unique = []
    seen = set()
    for token in candidates:
        token = token.strip()
        if token and token not in seen:
            seen.add(token)
            unique.append(token)
    return unique


def scan_reqable_for_token(
    capture_dir,
    expected_user_id=None,
    scan_limit=1200,
    max_file_size=2_000_000,
    min_seconds=300,
):
    """Scan recent Reqable files for a non-expired library JWT."""
    result = {
        "token": None,
        "source": None,
        "exp": 0,
        "scanned": 0,
        "total_files": 0,
        "skipped_large": 0,
        "token_candidates": 0,
        "library_candidates": 0,
        "expired_matches": 0,
        "mismatched_library_tokens": 0,
        "other_jwts": 0,
        "opaque_tokens": 0,
        "best_expired": None,
    }

    root = Path(capture_dir)
    if not root.is_dir():
        return result

    files = [path for path in root.rglob("*.reqable") if path.is_file()]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    result["total_files"] = len(files)

    seen_tokens = set()
    now = time.time()

    for path in files[:scan_limit]:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size == 0:
            continue
        if size > max_file_size:
            result["skipped_large"] += 1
            continue

        result["scanned"] += 1
        try:
            text = decode_body(path.read_bytes())
        except Exception:
            continue
        if not text:
            continue

        for token in extract_token_candidates(text):
            if token in seen_tokens:
                continue
            seen_tokens.add(token)
            result["token_candidates"] += 1

            payload = decode_jwt_payload(token)
            if not payload:
                result["opaque_tokens"] += 1
                continue

            user_id = library_user_id(payload)
            exp = jwt_exp(payload)
            if not user_id:
                result["other_jwts"] += 1
                continue

            result["library_candidates"] += 1
            if expected_user_id and user_id != str(expected_user_id):
                result["mismatched_library_tokens"] += 1
                continue

            rel = path.relative_to(root).as_posix()
            if not exp or now >= exp - min_seconds:
                result["expired_matches"] += 1
                if result["best_expired"] is None or exp > result["best_expired"]["exp"]:
                    result["best_expired"] = {"source": rel, "exp": exp}
                continue

            result["token"] = token
            result["source"] = rel
            result["exp"] = exp
            return result

    return result

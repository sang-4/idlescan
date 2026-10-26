"""Consistent secret redaction for logs, audit rows, errors and API responses.

RPC providers commonly put API keys in the URL query string or path, and
webhook URLs can be bearer-like secrets themselves.  Nothing user-facing should
ever need the credential part, so URLs are reduced to scheme + host + path.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_URL = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
_BEARER = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)\S+")
_KEY_QUERY = re.compile(r"(?i)([?&](?:api[_-]?key|apikey|key|token|secret)=)[^&\s]+")


def redact_url(url: str) -> str:
    """Strip credentials, query string and fragment from one URL."""
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return "[invalid url]"
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path or "/", "", ""))


def redact_text(value: str) -> str:
    """Redact URLs and bearer tokens embedded in free-form text."""
    text = str(value)
    text = _BEARER.sub(r"\1[redacted]", text)
    text = _URL.sub(lambda match: redact_url(match.group(0)), text)
    text = _KEY_QUERY.sub(r"\1[redacted]", text)
    return text

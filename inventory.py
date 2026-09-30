"""Read explicitly configured inventories before scheduling tasks."""

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler, Request, build_opener,
)

from genesis_core import BACKENDS, GenesisCore

MAX_BYTES = 1_000_000


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Inventory redirects are disabled")


def fetch_inventory(url: str) -> dict:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password
            or parsed.fragment or "010101" in parsed.netloc):
        raise ValueError("Configure a valid HTTPS inventory endpoint")
    request = Request(url, headers={"Accept": "application/json"})
    with build_opener(RejectRedirects()).open(request, timeout=10) as response:
        data = response.read(MAX_BYTES + 1)
    return _decode(data)


def read_inventory(path: str) -> dict:
    with Path(path).open("rb") as stream:
        return _decode(stream.read(MAX_BYTES + 1))


def _decode(data: bytes) -> dict:
    if len(data) > MAX_BYTES:
        raise ValueError("Inventory exceeds the size limit")
    document = json.loads(data)
    if not isinstance(document, dict):
        raise ValueError("Inventory must be an object")
    return document


def build_core(document: dict, max_age_seconds: int = 120,
               now: datetime | None = None) -> GenesisCore:
    """Create a fresh snapshot, never overwrite active reservations."""
    if type(max_age_seconds) is not int or max_age_seconds < 1:
        raise ValueError("Invalid maximum inventory age")
    if not isinstance(document, dict):
        raise ValueError("Inventory must be an object")
    timestamp = document.get("updated_at")
    if not isinstance(timestamp, str):
        raise ValueError("Inventory timestamp is required")
    try:
        observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Invalid inventory timestamp") from error
    current = now if now is not None else datetime.now(timezone.utc)
    if (observed.utcoffset() is None or current.utcoffset() is None):
        raise ValueError("Timestamps must include a timezone")
    age = (current - observed).total_seconds()
    if age < 0 or age > max_age_seconds:
        raise ValueError("Inventory is stale or future-dated")
    servers = document.get("servers")
    if not isinstance(servers, list):
        raise ValueError("Inventory must contain a servers array")

    core = GenesisCore()
    names = set()
    for server in servers:
        if not isinstance(server, dict):
            raise ValueError("Each server must be an object")
        name = server.get("name")
        backend = server.get("backend")
        memory = server.get("allocatable_vram_mb")
        slots = server.get("available_task_slots")
        enabled = server.get("enabled")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("Server names must be nonempty and unique")
        names.add(name)
        if not isinstance(backend, str) or backend not in BACKENDS:
            raise ValueError("Unsupported backend")
        if type(memory) is not int or memory < 0:
            raise ValueError("Invalid allocatable memory")
        if type(slots) is not int or slots < 0:
            raise ValueError("Invalid available slots")
        if type(enabled) is not bool:
            raise ValueError("An explicit enabled flag is required")
        if enabled and (
            "010101" in name
            or any("010101" in str(server.get(key, ""))
                   for key in ("host", "worker_url"))
        ):
            raise ValueError("Enabled servers cannot contain placeholders")
        if enabled and slots > 0:
            core.register(name, backend, memory, slots)
    return core

"""Coolhand client for submitting API interactions."""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .types import Config, RequestData, ResponseData
from .version import __version__

logger = logging.getLogger(__name__)

# Sensitive headers to mask
SENSITIVE_HEADERS = [
    "authorization",
    "api-key",
    "x-api-key",
    "openai-api-key",
    "anthropic-api-key",
]


BASE_URL = "https://coolhandlabs.com"


def _get_default_config() -> Config:
    """Get default configuration from environment."""
    return {
        "api_key": os.getenv("COOLHAND_API_KEY", "demo-key"),
        "silent": os.getenv("COOLHAND_SILENT", "true").lower() == "true",
        "auto_submit": True,
        "session_id": f"session_{int(time.time() * 1000)}",
    }


def _mask_value(value: str) -> str:
    """Mask a sensitive value, keeping first/last 4 chars."""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def _sanitize_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Sanitize sensitive headers."""
    result = {}
    for key, value in headers.items():
        if any(s in key.lower() for s in SENSITIVE_HEADERS):
            result[key] = _mask_value(str(value))
        else:
            result[key] = value
    return result


def _parse_body(body: Optional[Union[str, bytes, Dict]]) -> Optional[Union[str, Dict]]:
    """Parse body to JSON object if possible, otherwise return as string."""
    if body is None:
        return None

    # Already a dict
    if isinstance(body, dict):
        return body

    # Convert bytes to string
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8")
        except Exception:
            return str(body)

    # Try to parse as JSON
    if isinstance(body, str):
        try:
            return json.loads(body)
        except Exception:
            return body

    return str(body)


def _to_iso8601(timestamp: float) -> str:
    """Convert Unix timestamp to ISO 8601 string."""
    return (
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


class CoolhandClient:
    """Simple client for submitting interactions to Coolhand."""

    def __init__(self, config: Optional[Config] = None, **kwargs):
        """Initialize the client."""
        self.config = _get_default_config()
        if config:
            self.config.update(config)
        self.config.update(kwargs)

        self._queue: List[Dict[str, Any]] = []
        self._interaction_count = 0

        if not self.config.get("silent"):
            logging.basicConfig(level=logging.INFO)

    @property
    def session_id(self) -> str:
        return self.config.get("session_id", "")

    def log_interaction(
        self,
        request: RequestData,
        response: Optional[ResponseData] = None,
        error: Optional[str] = None,
    ) -> None:
        """Log an API interaction in flat format matching Ruby/Node SDKs."""
        self._interaction_count += 1

        # Get timestamps
        req_timestamp = request.get("timestamp", time.time())
        res_timestamp = (
            response.get("timestamp", time.time()) if response else time.time()
        )
        duration_seconds = response.get("duration", 0.0) if response else 0.0

        # Build flat interaction data (matching Ruby/Node format)
        interaction = {
            "id": str(uuid.uuid4()),
            "timestamp": _to_iso8601(req_timestamp),
            "method": request.get("method", "").lower(),
            "url": request.get("url", ""),
            "headers": _sanitize_headers(request.get("headers", {})),
            "request_body": _parse_body(request.get("body")),
            "response_headers": _sanitize_headers(response.get("headers", {}))
            if response
            else {},
            "response_body": _parse_body(response.get("body")) if response else None,
            "status_code": response.get("status_code", 0) if response else 0,
            "duration_ms": round(duration_seconds * 1000, 2),
            "completed_at": _to_iso8601(res_timestamp),
            "is_streaming": response.get("is_streaming", False) if response else False,
        }

        # Debug output when not silent
        if not self.config.get("silent"):
            url = request.get("url", "unknown")
            method = request.get("method", "unknown")
            status = response.get("status_code") if response else "error"
            logger.info(f"Captured: {method} {url} -> {status}")

        self._queue.append(interaction)

        # Auto-submit if enabled
        if self.config.get("auto_submit") and len(self._queue) >= 1:
            self.flush()

    def flush(self) -> bool:
        """Submit queued interactions to Coolhand API."""
        if not self._queue:
            return True

        api_key = self.config.get("api_key")
        if not api_key or api_key == "demo-key":
            logger.debug("No API key configured, skipping submission")
            self._queue.clear()
            return True

        success_count = 0

        for interaction in self._queue:
            try:
                payload = {
                    "llm_request_log": {
                        "raw_request": interaction,
                        "collector": f"coolhand-python-{__version__}-auto-monitor",
                    }
                }

                request = Request(
                    url=f"{BASE_URL}/api/v2/llm_request_logs",
                    data=json.dumps(payload, default=str).encode("utf-8"),
                    headers={
                        "X-API-Key": api_key,
                        "Content-Type": "application/json",
                        "User-Agent": f"coolhand-python/{__version__}",
                    },
                    method="POST",
                )

                with urlopen(request, timeout=10) as resp:
                    if resp.status == 200 or resp.status == 201:
                        success_count += 1

            except (HTTPError, URLError) as e:
                logger.warning(f"Failed to submit interaction: {e}")
            except Exception as e:
                logger.warning(f"Unexpected error submitting interaction: {e}")

        if success_count > 0:
            queue_len = len(self._queue)
            logger.info(
                f"Successfully submitted {success_count}/{queue_len} interactions"
            )

        self._queue.clear()
        return success_count == len(self._queue)

    def get_stats(self) -> Dict[str, Any]:
        """Get client statistics."""
        # Check if httpx is patched
        patched_libs = []
        try:
            from . import interceptor

            if interceptor.is_patched():
                patched_libs = ["httpx.Client.send", "httpx.AsyncClient.send"]
        except Exception:
            pass

        return {
            "config": {
                "has_api_key": bool(self.config.get("api_key")),
            },
            "monitoring": {
                "enabled": True,
                "patched_libraries": patched_libs,
            },
            "logging": {
                "session_id": self.session_id,
                "interaction_count": self._interaction_count,
                "queue_size": len(self._queue),
            },
        }

    def shutdown(self) -> None:
        """Flush and cleanup."""
        logger.info("Shutting down Coolhand...")
        self.flush()
        logger.info("Coolhand shutdown complete")


# Global instance
_instance: Optional[CoolhandClient] = None


def get_instance() -> Optional[CoolhandClient]:
    """Get the global client instance."""
    return _instance


def set_instance(instance: CoolhandClient) -> None:
    """Set the global client instance."""
    global _instance
    _instance = instance


def initialize(config: Optional[Config] = None, **kwargs) -> CoolhandClient:
    """Initialize the global client."""
    global _instance
    if _instance is None:
        _instance = CoolhandClient(config, **kwargs)
    return _instance

"""Thin async client for the YNAB API.

Every tool builds a client from its ``Context`` so the caller's OAuth token is used.
Non-2xx responses are translated into Arcade errors with messages a model can act on.
"""

from typing import Any

import httpx
from arcade_mcp_server.exceptions import (
    RetryableToolError,
    ToolExecutionError,
    UpstreamError,
    UpstreamRateLimitError,
)

BASE_URL = "https://api.ynab.com/v1"
TIMEOUT_SECONDS = 30.0
DEFAULT_RETRY_AFTER_MS = 60_000

# Overridden in tests with an httpx.MockTransport; None means real network access.
TRANSPORT: httpx.AsyncBaseTransport | None = None


class YnabClient:
    def __init__(self, token: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "arcade-ynab-mcp",
        }

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        empty_on_404: bool = False,
    ) -> dict[str, Any]:
        """Send a request and return the response's ``data`` object.

        With ``empty_on_404``, a 404 returns ``{}``. YNAB's list endpoints answer 404
        ("No transactions were found") when a collection is empty.
        """
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        async with httpx.AsyncClient(
            base_url=BASE_URL,
            headers=self._headers,
            timeout=TIMEOUT_SECONDS,
            transport=TRANSPORT,
        ) as client:
            response = await client.request(method, path, params=clean_params, json=json)
        if response.status_code == 404 and empty_on_404:
            return {}
        if response.is_error:
            _raise_for_error(response)
        return response.json().get("data", {})

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        return await self.request("GET", path, params=params)

    async def get_list(self, path: str, **params: Any) -> dict[str, Any]:
        """GET a collection; an empty collection (404) returns ``{}``."""
        return await self.request("GET", path, params=params, empty_on_404=True)

    async def post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", path, json=json)

    async def put(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        return await self.request("PUT", path, json=json)

    async def patch(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        return await self.request("PATCH", path, json=json)

    async def delete(self, path: str) -> dict[str, Any]:
        return await self.request("DELETE", path)


def client_from_context(context: Any) -> YnabClient:
    token = context.get_auth_token_or_empty()
    if not token:
        raise ToolExecutionError(
            "No YNAB authorization is available for this user.",
            developer_message="Context had no OAuth token for provider 'ynab'.",
        )
    return YnabClient(token)


def _error_details(response: httpx.Response) -> tuple[str, str, str]:
    """Return YNAB's (id, name, detail) error triple, tolerating non-JSON bodies."""
    try:
        body = response.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        error = {}
    return (
        str(error.get("id", response.status_code)),
        str(error.get("name", "")),
        str(error.get("detail", "")),
    )


def _raise_for_error(response: httpx.Response) -> None:
    status = response.status_code
    error_id, name, detail = _error_details(response)
    request = response.request
    dev = f"YNAB {request.method} {request.url.path} -> {status} {error_id} {name}: {detail}"

    if status == 400:
        raise RetryableToolError(
            f"YNAB rejected the request: {detail or 'validation failed'}",
            developer_message=dev,
            additional_prompt_content="Fix the invalid parameters described above and retry.",
        )
    if status == 401:
        raise UpstreamError(
            "YNAB rejected the authorization. The token is missing, expired or revoked; "
            "the user needs to re-authorize the YNAB connection.",
            developer_message=dev,
            status_code=status,
        )
    if status == 403:
        if error_id == "403.1":
            message = "The user's YNAB subscription has lapsed."
        elif error_id == "403.2":
            message = "The user's YNAB trial has expired."
        elif error_id == "403.3":
            message = "The YNAB authorization does not allow this action (read-only access)."
        elif error_id == "403.4":
            message = "The YNAB plan has reached its data limit."
        elif error_id == "403.5":
            message = "The user must confirm their YNAB email address before using the API."
        else:
            message = f"YNAB refused the request: {detail or 'forbidden'}"
        raise UpstreamError(message, developer_message=dev, status_code=status)
    if status == 404:
        raise RetryableToolError(
            f"YNAB could not find that resource: {detail or 'not found'}",
            developer_message=dev,
            additional_prompt_content=(
                "Check the IDs used. List the relevant plans, accounts, categories, payees or "
                "transactions to find valid IDs, then retry."
            ),
        )
    if status == 429:
        raise UpstreamRateLimitError(
            "YNAB's rate limit (200 requests per hour per user) was reached. Try again later.",
            retry_after_ms=_retry_after_ms(response),
            developer_message=dev,
        )
    raise UpstreamError(
        f"YNAB returned an error ({status}): {detail or name or 'unexpected error'}",
        developer_message=dev,
        status_code=status,
    )


def _retry_after_ms(response: httpx.Response) -> int:
    try:
        return int(float(response.headers["Retry-After"]) * 1000)
    except (KeyError, ValueError):
        return DEFAULT_RETRY_AFTER_MS

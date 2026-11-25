"""HTTP fetching with a shared cache, a concurrency cap, and a request rate."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

import httpx

from migcheck.models import FetchResult
from migcheck.urls import normalize_url

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class RateLimiter:
    """Space out request starts. The first acquire is immediate."""

    def __init__(
        self, rps: float, *, clock: Clock | None = None, sleep: Sleep | None = None
    ) -> None:
        if rps <= 0:
            raise ValueError("rps must be positive")
        self._interval = 1.0 / rps
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = self._clock()
            wait = self._next - now
            if wait < 0:
                wait = 0.0
            base = now if self._next < now else self._next
            self._next = base + self._interval
        if wait > 0:
            await self._sleep(wait)


class Fetcher(Protocol):
    async def get(self, url: str) -> FetchResult: ...

    async def aclose(self) -> None: ...


class FixtureFetcher:
    """Resolve responses from a prepared map. Unknown URLs are errors, not network calls."""

    def __init__(self, responses: dict[str, FetchResult]) -> None:
        self._responses = responses

    async def get(self, url: str) -> FetchResult:
        key = normalize_url(url)
        found = self._responses.get(key)
        if found is not None:
            return found
        return FetchResult(
            url=key,
            status=None,
            headers={},
            body="",
            content_type="",
            location=None,
            error=f"No fixture response for {key}",
        )

    async def aclose(self) -> None:
        return None


class HttpFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        limiter: RateLimiter,
        semaphore: asyncio.Semaphore,
        *,
        max_bytes: int,
    ) -> None:
        self._client = client
        self._limiter = limiter
        self._semaphore = semaphore
        self._max_bytes = max_bytes
        self._cache: dict[str, FetchResult] = {}
        self._inflight: dict[str, asyncio.Future[FetchResult]] = {}
        self._cache_lock = asyncio.Lock()

    async def get(self, url: str) -> FetchResult:
        key = normalize_url(url)
        async with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            inflight = self._inflight.get(key)
            if inflight is None:
                future: asyncio.Future[FetchResult] = asyncio.get_running_loop().create_future()
                self._inflight[key] = future
                owner = True
            else:
                future = inflight
                owner = False
        if not owner:
            return await future
        try:
            await self._limiter.acquire()
            async with self._semaphore:
                result = await self._request(key)
        except Exception as exc:
            result = _error_result(key, f"{type(exc).__name__}: {exc}")
        async with self._cache_lock:
            self._cache[key] = result
            self._inflight.pop(key, None)
        if not future.done():
            future.set_result(result)
        return result

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, url: str) -> FetchResult:
        try:
            async with self._client.stream("GET", url, follow_redirects=False) as response:
                chunks: list[bytes] = []
                total = 0
                truncated = False
                async for chunk in response.aiter_bytes():
                    if total >= self._max_bytes:
                        truncated = True
                        break
                    room = self._max_bytes - total
                    if len(chunk) > room:
                        chunks.append(chunk[:room])
                        total += room
                        truncated = True
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                raw = b"".join(chunks)
                encoding = response.encoding or "utf-8"
                body = raw.decode(encoding, errors="replace")
                headers = {key.lower(): value for key, value in response.headers.items()}
                return FetchResult(
                    url=url,
                    status=response.status_code,
                    headers=headers,
                    body=body,
                    content_type=headers.get("content-type", ""),
                    location=headers.get("location"),
                    error=None,
                    truncated=truncated,
                )
        except httpx.HTTPError as exc:
            return _error_result(url, f"{type(exc).__name__}: {exc}")


def make_client(
    *,
    timeout: float,
    concurrency: int,
    user_agent: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(timeout),
        follow_redirects=False,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
        },
        limits=httpx.Limits(
            max_connections=concurrency,
            max_keepalive_connections=concurrency,
        ),
    )


def _error_result(url: str, message: str) -> FetchResult:
    return FetchResult(
        url=url,
        status=None,
        headers={},
        body="",
        content_type="",
        location=None,
        error=message,
    )

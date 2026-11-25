import asyncio

import httpx
import pytest

from migcheck.fetch import HttpFetcher, RateLimiter, make_client


def test_rate_limiter_spaces_starts() -> None:
    clock = {"now": 0.0}
    sleeps: list[float] = []

    def now() -> float:
        return clock["now"]

    async def sleep(delay: float) -> None:
        sleeps.append(delay)
        clock["now"] += delay

    async def run() -> None:
        limiter = RateLimiter(10, clock=now, sleep=sleep)
        for _ in range(4):
            await limiter.acquire()

    asyncio.run(run())
    assert sleeps == pytest.approx([0.1, 0.1, 0.1])


def test_client_does_not_follow_and_truncates() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["user-agent"] = request.headers["user-agent"]
        if request.url.path == "/go":
            return httpx.Response(301, headers={"location": "/stop"})
        return httpx.Response(200, text="x" * 500, headers={"content-type": "text/html"})

    async def run() -> None:
        client = make_client(
            timeout=5,
            concurrency=2,
            user_agent="migcheck-test",
            transport=httpx.MockTransport(handler),
        )
        fetcher = HttpFetcher(client, RateLimiter(1000), asyncio.Semaphore(2), max_bytes=100)
        redirect = await fetcher.get("https://example.test/go")
        assert redirect.status == 301
        assert redirect.location == "/stop"
        body = await fetcher.get("https://example.test/big")
        assert body.truncated
        assert len(body.body.encode()) <= 100
        again = await fetcher.get("https://example.test/big")
        assert again is body
        await fetcher.aclose()

    asyncio.run(run())
    assert seen["user-agent"] == "migcheck-test"

"""Dependency-free soak runner (uses the backend venv's httpx/websockets).

Same coverage as k6-chat.js for environments without k6:
    cd backend && uv run python ../load/soak.py --minutes 3 --vus 10

Drives non-LLM REST (healthz, session CRUD, list, search) + WS
connect/ping concurrently; reports rps, error count, and latency
p50/p95/p99 per endpoint at the end.
"""

import argparse
import asyncio
import json
import statistics
import time
from collections import defaultdict

import httpx
import websockets

BASE = "http://localhost:8000"


async def rest_vu(
    stop_at: float, latencies: dict[str, list[float]], errors: list[str]
) -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=10.0) as client:
        login = await client.post("/api/v1/auth/dev-login")
        client.cookies = login.cookies
        while time.monotonic() < stop_at:
            for label, method, url in (
                ("healthz", "GET", "/healthz"),
                ("create", "POST", "/api/v1/chat/sessions"),
                ("list", "GET", "/api/v1/chat/sessions?limit=50"),
                ("search", "GET", "/api/v1/chat/search?q=test"),
            ):
                start = time.monotonic()
                try:
                    response = await client.request(method, url)
                    latencies[label].append((time.monotonic() - start) * 1000)
                    if response.status_code >= 400:
                        errors.append(f"{label}:{response.status_code}")
                    if label == "create" and response.status_code == 201:
                        sid = response.json()["id"]
                        deletion = await client.delete(f"/api/v1/chat/sessions/{sid}")
                        if deletion.status_code >= 400:
                            errors.append(f"delete:{deletion.status_code}")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{label}:{type(exc).__name__}")
            await asyncio.sleep(0.2)


async def ws_vu(stop_at: float, latencies: dict[str, list[float]], errors: list[str]) -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=10.0) as client:
        login = await client.post("/api/v1/auth/dev-login")
        cookie = login.cookies.get("finzorr_session", "")
    while time.monotonic() < stop_at:
        start = time.monotonic()
        try:
            async with websockets.connect(
                f"{BASE.replace('http', 'ws')}/ws/chat",
                additional_headers={"Cookie": f"finzorr_session={cookie}"},
            ) as socket:
                await socket.send(json.dumps({"type": "ping"}))
                frame = json.loads(await asyncio.wait_for(socket.recv(), timeout=5))
                if frame.get("type") != "pong":
                    errors.append("ws:not-pong")
                latencies["ws_ping"].append((time.monotonic() - start) * 1000)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"ws:{type(exc).__name__}")
        await asyncio.sleep(0.5)


async def main(minutes: float, vus: int) -> None:
    stop_at = time.monotonic() + minutes * 60
    latencies: dict[str, list[float]] = defaultdict(list)
    errors: list[str] = []
    tasks = [asyncio.create_task(rest_vu(stop_at, latencies, errors)) for _ in range(vus)]
    tasks += [asyncio.create_task(ws_vu(stop_at, latencies, errors)) for _ in range(max(vus // 2, 1))]
    await asyncio.gather(*tasks)
    total = sum(len(v) for v in latencies.values())
    print(f"\nsoak: {minutes}min, {vus} REST VUs — {total} requests, {len(errors)} errors")
    print(f"{'endpoint':<10} {'n':>6} {'p50':>8} {'p95':>8} {'p99':>8}")
    for label, values in sorted(latencies.items()):
        if len(values) < 2:  # quantiles() raises below 2 samples
            print(f"{label:<10} {len(values):>6}  (too few samples)")
            continue
        quantiles = statistics.quantiles(values, n=100)
        print(
            f"{label:<10} {len(values):>6} {quantiles[49]:>7.1f}ms "
            f"{quantiles[94]:>7.1f}ms {quantiles[98]:>7.1f}ms"
        )
    if errors:
        from collections import Counter

        print("errors:", dict(Counter(errors)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=3)
    parser.add_argument("--vus", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(main(args.minutes, args.vus))

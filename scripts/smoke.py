"""Smoke test a deployed endpoint: health, page config, and one real question over SSE.

uv run python scripts/smoke.py <function url> <app token secret arn> [question]
"""

from __future__ import annotations

import json
import sys
import time

import boto3
import httpx

QUESTION = "東京→ロンドンのビジネスクラス特典航空券は何マイル必要？"


def main() -> int:
    url, secret_arn = sys.argv[1].rstrip("/"), sys.argv[2]
    question = sys.argv[3] if len(sys.argv) > 3 else QUESTION
    token = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)["SecretString"]

    t0 = time.perf_counter()
    r = httpx.get(f"{url}/healthz", timeout=60)
    print(
        f"healthz: {r.status_code} {r.text} ({time.perf_counter() - t0:.1f}s, includes cold start)"
    )
    if r.status_code != 200:
        return 1
    # The endpoint is public now (design.md §4.8); the smoke question uses the owner token
    # so it does not spend the public budget. /config is what the page loads first.
    r = httpx.get(f"{url}/config", timeout=30)
    print(f"config: {r.status_code} {r.text}")
    if r.status_code != 200:
        return 1

    t0 = time.perf_counter()
    first_byte = None
    events: list[tuple[str, dict[str, object]]] = []
    with httpx.stream(
        "POST",
        f"{url}/ask",
        json={"question": question},
        headers={"Authorization": f"Bearer {token}"},
        timeout=180,
    ) as resp:
        print(f"ask: {resp.status_code} {resp.headers.get('content-type')}")
        for chunk in resp.iter_text():
            first_byte = first_byte or time.perf_counter() - t0
            for block in chunk.strip().split("\n\n"):
                lines = dict(
                    ln.split(": ", 1)
                    for ln in block.splitlines()
                    if ": " in ln and not ln.startswith(":")
                )
                if "event" in lines:
                    events.append((lines["event"], json.loads(lines["data"])))
    print(f"first byte after {first_byte:.1f}s (streaming works if this is well below the total)")
    print(f"events: {[e for e, _ in events]} total {time.perf_counter() - t0:.1f}s")
    for name, data in events:
        if name == "answer":
            print(str(data["text"])[:400])
        if name == "done":
            print(json.dumps(data, ensure_ascii=False))
    return 0 if [e for e, _ in events][-2:] == ["answer", "done"] else 1


if __name__ == "__main__":
    sys.exit(main())

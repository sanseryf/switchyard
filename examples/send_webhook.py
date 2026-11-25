#!/usr/bin/env python3
"""Send a signed GitHub-style webhook, for local demos.

    $env:GITHUB_WEBHOOK_SECRET = "dev-secret"
    python examples/send_webhook.py --event member --delivery d-001 \
        --payload examples/bad-member.json
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080/webhooks/github")
    parser.add_argument("--event", default="member")
    parser.add_argument("--delivery", default="demo-1")
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--secret-env", default="GITHUB_WEBHOOK_SECRET")
    args = parser.parse_args(argv)

    secret = os.environ.get(args.secret_env)
    if not secret:
        print(f"{args.secret_env} is not set", file=sys.stderr)
        return 2
    body = args.payload.read_bytes()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        args.url,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": args.event,
            "X-GitHub-Delivery": args.delivery,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            print(response.status, response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(exc.code, exc.read().decode("utf-8"), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

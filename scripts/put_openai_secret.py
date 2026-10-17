"""Store the OpenAI key for Lambda in Secrets Manager (never in Terraform state, R20).

    uv run --env-file .env python scripts/put_openai_secret.py <secret id>

Uses OPENAI_LAMBDA_API_KEY if set (recommended: a separate project key for the cloud,
so the local and cloud keys can be revoked independently), else OPENAI_API_KEY.
The key is passed through the SDK, not the command line, so it never shows up in `ps`.
"""

from __future__ import annotations

import os
import sys

import boto3
from botocore.exceptions import ClientError


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    secret_id = sys.argv[1]
    key = os.environ.get("OPENAI_LAMBDA_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    if not key.startswith("sk-"):
        print("no OpenAI key in OPENAI_LAMBDA_API_KEY / OPENAI_API_KEY")
        return 1
    if not os.environ.get("OPENAI_LAMBDA_API_KEY"):
        print("note: using OPENAI_API_KEY; consider a separate OPENAI_LAMBDA_API_KEY for the cloud")
    sm = boto3.client("secretsmanager")
    try:
        current = sm.get_secret_value(SecretId=secret_id)["SecretString"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        current = ""
    if current == key:
        print(f"{secret_id}: unchanged")
        return 0
    sm.put_secret_value(SecretId=secret_id, SecretString=key)
    print(f"{secret_id}: updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())

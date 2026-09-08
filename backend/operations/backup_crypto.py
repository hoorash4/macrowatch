from __future__ import annotations

import hashlib
import json
import os


def main() -> int:
    payload = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    private_key = payload.get("private_key", "")
    if not private_key:
        raise RuntimeError("Service account private key is missing.")

    material = "MacroWatch backup encryption v1\0" + private_key
    print(hashlib.sha256(material.encode("utf-8")).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


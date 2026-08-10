"""Build the registry artifact that cm_mcp_engine consumes.

Merge to main is approval; this turns the approved set into one file the engine
can download, pin, and serve. It re-validates everything on the way out, so a
registry that exists is a registry that passed the gate.

Usage:
    python scripts/build_registry.py                       # -> dist/registry.generated.json
    python scripts/build_registry.py --out some/path.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "schema" / "tool-contract.v1.json"
CONTRACTS_DIR = REPO_ROOT / "contracts"
DEFAULT_OUT = REPO_ROOT / "dist" / "registry.generated.json"


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:  # noqa: BLE001 - a build outside a checkout is still valid
        return "unknown"


def tool_names(contract: dict) -> list[str]:
    if contract.get("kind") == "multi-tool":
        return [t.get("interface", {}).get("name", "?") for t in contract.get("tools", [])]
    if contract.get("kind") == "single-tool":
        return [contract.get("interface", {}).get("name", "?")]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Build registry.generated.json.")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    validator = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))

    contracts: list[dict] = []
    names: list[str] = []
    failures: list[str] = []

    for path in sorted(CONTRACTS_DIR.rglob("*.json")):
        contract = json.loads(path.read_text(encoding="utf-8"))

        # Belt and braces: the PR gate already validated these, but a registry
        # is published without a human in the loop, so it re-checks.
        if errors := sorted(validator.iter_errors(contract), key=str):
            failures.append(f"{path.name}: {errors[0].message}")
            continue

        entry_names = tool_names(contract)
        clashes = sorted(set(entry_names) & set(names))
        if clashes:
            failures.append(f"{path.name}: tool name(s) already in the registry: {clashes}")
            continue

        contracts.append(contract)
        names.extend(entry_names)

    if failures:
        print("Refusing to publish a registry with invalid contracts:\n")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sourceRepo": "cm_mcp_contracts",
        "sourceCommit": git_sha(),
        "schemaId": json.loads(SCHEMA_PATH.read_text(encoding="utf-8")).get("$id"),
        "toolCount": len(names),
        "toolNames": sorted(names),
        "contracts": contracts,
    }

    body = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")

    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    print(f"Wrote {out}")
    print(f"  contracts : {len(contracts)}")
    print(f"  tools     : {len(names)} -> {', '.join(sorted(names))}")
    print(f"  sha256    : {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Build the registry the engine consumes.

Merge to main is approval; this turns the approved set into an artifact the engine
can download, pin, and serve. It re-validates everything on the way out, so a
registry that exists is a registry that passed the gate.

**Layout: an index plus one file per contract.**

    registry/
      registry.json                    <- provenance and a hash per contract
      contracts/list_categories.json   <- the contract, byte-for-byte as submitted

Not one file with every contract inlined. At a few contracts the difference is
cosmetic; at a few hundred it decides whether the engine's pin PR is reviewable.
One file per contract means a diff shows *which* contract changed, `git blame`
answers "who changed this tool", and two registry updates touching different
tools do not conflict.

The index is what makes the directory a registry rather than a folder. It carries
the provenance the engine pins -- source commit, build time, schema id -- and a
sha256 per entry, so the engine serves exactly what was published: a file added to
`contracts/` afterwards is not listed and is not served, and an edited one fails
its hash. Approved stops meaning "present on disk".

Usage:
    python scripts/build_registry.py                    # -> dist/registry/
    python scripts/build_registry.py --out build/reg
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_contracts import dependency_problems, resolver_problems  # noqa: E402

SCHEMA_PATH = REPO_ROOT / "schema" / "tool-contract.v2.json"
CONTRACTS_DIR = REPO_ROOT / "contracts"
DEFAULT_OUT = REPO_ROOT / "dist" / "registry"

INDEX_NAME = "registry.json"
CONTRACTS_SUBDIR = "contracts"


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:  # noqa: BLE001 - a build outside a checkout is still valid
        return "unknown"


def tool_name(contract: dict) -> str | None:
    """One endpoint per contract, so at most one name."""
    return contract.get("interface", {}).get("name")


def canonical(contract: dict) -> bytes:
    """The exact bytes that get written and hashed.

    Formatted rather than copied verbatim, so the hash depends on the contract's
    content and not on how its author happened to indent it.

    Bytes, and written with `write_bytes`, because text mode rewrites newlines on
    Windows: the file on disk would end up CRLF while the hash was taken over LF,
    and the artifact would fail its own integrity check on the platform it was
    built on. An artifact has to mean the same thing everywhere it is produced.
    """
    body = json.dumps(contract, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    return body.encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the registry artifact.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output directory")
    args = parser.parse_args()

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    entries: list[dict] = []
    bodies: dict[str, bytes] = {}
    names: list[str] = []
    accepted: dict[str, dict] = {}
    failures: list[str] = []

    for path in sorted(CONTRACTS_DIR.rglob("*.json")):
        contract = json.loads(path.read_text(encoding="utf-8"))

        # Belt and braces: the PR gate already validated these, but a registry is
        # published without a human in the loop, so it re-checks.
        if errors := sorted(validator.iter_errors(contract), key=str):
            failures.append(f"{path.name}: {errors[0].message}")
            continue

        name = tool_name(contract)
        if not name:
            failures.append(f"{path.name}: interface.name is missing")
            continue

        if name in names:
            failures.append(f"{path.name}: tool name {name!r} is already in the registry")
            continue

        body = canonical(contract)
        relative = f"{CONTRACTS_SUBDIR}/{name}.json"
        bodies[relative] = body
        names.append(name)
        accepted[name] = contract
        entries.append(
            {
                "name": name,
                "version": contract.get("contractVersion", "0.0.0"),
                "path": relative,
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )

    # The PR gate checks this too, against the approved lane on disk. Here it is
    # checked against what is actually about to be *published*, which is the set
    # the engine will serve -- a contract dropped above for being invalid takes its
    # tool name out of the registry, and anything depending on it is now dangling.
    known = set(names)
    for name, contract in accepted.items():
        failures.extend(f"{name}.json: {problem}" for problem in dependency_problems(contract, known))
        failures.extend(f"{name}.json: {problem}" for problem in resolver_problems(contract, accepted))

    if failures:
        print("Refusing to publish a registry with invalid contracts:\n")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    # Sorted so a rebuild of an unchanged registry produces identical entries.
    contracts = sorted(entries, key=lambda entry: entry["name"])

    index = {
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "sourceRepo": "cm_mcp_contracts",
        "sourceCommit": git_sha(),
        "schemaId": schema.get("$id"),
        "layout": "index",
        # What the registry *is*, with the build stamp left out: names, versions and
        # hashes. Every publish writes a fresh generatedAt and sourceCommit, so
        # without this the engine would open a pin PR for a commit that touched a
        # script and changed no contract -- review fatigue at exactly the scale this
        # layout exists to survive. consume-registry compares this and stops.
        "contentHash": hashlib.sha256(
            json.dumps(contracts, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "toolCount": len(names),
        "toolNames": sorted(names),
        "contracts": contracts,
    }

    out = Path(args.out)
    # Rebuilt from scratch: a contract deleted upstream must disappear here, and a
    # leftover file would be served by an engine that trusted the directory.
    if out.exists():
        shutil.rmtree(out)
    (out / CONTRACTS_SUBDIR).mkdir(parents=True)

    for relative, body in bodies.items():
        (out / relative).write_bytes(body)

    index_body = (json.dumps(index, indent=2) + "\n").encode("utf-8")
    (out / INDEX_NAME).write_bytes(index_body)

    print(f"Wrote {out}")
    print(f"  index     : {INDEX_NAME}  ({hashlib.sha256(index_body).hexdigest()[:12]})")
    print(f"  contracts : {len(entries)} file(s) in {CONTRACTS_SUBDIR}/")
    for entry in index["contracts"]:
        print(f"    {entry['name']:<28} v{entry['version']:<8} {entry['sha256'][:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

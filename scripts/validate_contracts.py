"""Structural gate: validate contracts against the meta-schema.

Used by contract-gate.yml on every PR touching contracts/**, and by
demo_gate.ps1 to show a rejection live.

We wrap `jsonschema` rather than calling `check-jsonschema` directly (as the brief
sketched) for two reasons the shell cannot give us: schema/ and
contracts/templates/ must be excluded from the sweep, and errors need grouping per
file with a readable JSON path instead of a wall of anyOf/oneOf noise.

Usage:
    python scripts/validate_contracts.py                     # all approved contracts
    python scripts/validate_contracts.py path/a.json ...     # specific files
    python scripts/validate_contracts.py --dir tests/fixtures/invalid
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "schema" / "tool-contract.v1.json"
CONTRACTS_DIR = REPO_ROOT / "contracts"


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def discover(paths: list[str] | None, directory: str | None) -> list[Path]:
    if paths:
        return [Path(p).resolve() for p in paths]
    root = Path(directory).resolve() if directory else CONTRACTS_DIR
    return sorted(p for p in root.rglob("*.json"))


# Branch order inside the top-level `oneOf`, so a failure can be reported against the
# branch the author actually meant instead of all three at once.
KIND_BRANCH = {"single-tool": 0, "multi-tool": 1, "openapi-import": 2}


def _location(error) -> str:
    return "/".join(str(p) for p in error.absolute_path) or "(root)"


def _describe(error) -> list[str]:
    """Turn a jsonschema error into lines a contributor can act on.

    A top-level `oneOf` failure is the unreadable case: all three contract kinds report
    their own complaints, and the loudest one ("'package' is a required property") comes
    from a branch the author never intended. Reporting against the declared `kind` is the
    difference between a useful rejection and a confusing one.
    """
    if error.validator != "oneOf" or not error.context:
        return [f"{_location(error)}: {error.message}"]

    declared_kind = error.instance.get("kind") if isinstance(error.instance, dict) else None
    branch = KIND_BRANCH.get(declared_kind)

    if branch is None:
        return [f"(root): 'kind' must be one of {sorted(KIND_BRANCH)} -- got {declared_kind!r}"]

    relevant = [sub for sub in error.context if sub.schema_path and sub.schema_path[0] == branch]
    if not relevant:
        return [f"{_location(error)}: {error.message}"]

    lines: list[str] = []
    for sub in sorted(relevant, key=lambda e: list(e.absolute_path)):
        lines.extend(_describe_nested_oneof(sub) if sub.validator == "oneOf" and sub.context else [f"{_location(sub)}: {sub.message}"])
    return lines


def _describe_nested_oneof(error) -> list[str]:
    """Nested `oneOf`s (binding.type) are discriminated on a `type` const.

    If the declared type matches no branch, that IS the error -- say so, rather than
    reporting whichever branch happened to complain the loudest about something else.
    """
    allowed = [
        branch.get("properties", {}).get("type", {}).get("const")
        for branch in error.schema.get("oneOf", [])
    ]
    allowed = [a for a in allowed if a is not None]
    declared = error.instance.get("type") if isinstance(error.instance, dict) else None

    if allowed and declared not in allowed:
        return [f"{_location(error)}/type: must be one of {allowed} -- got {declared!r}"]

    branch = allowed.index(declared) if allowed else None
    matching = [sub for sub in error.context if branch is None or (sub.schema_path and sub.schema_path[0] == branch)]
    if not matching:
        return [f"{_location(error)}: {error.message}"]
    return [f"{_location(sub)}: {sub.message}" for sub in sorted(matching, key=lambda e: list(e.absolute_path))]


def validate_file(path: Path, validator: Draft202012Validator) -> list[str]:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"(root): not valid JSON -- {exc}"]

    errors = sorted(validator.iter_errors(contract), key=lambda e: list(e.absolute_path))
    # Deduplicate: oneOf branches often report the same underlying problem twice.
    seen, described = set(), []
    for error in errors:
        for line in _describe(error):
            if line not in seen:
                seen.add(line)
                described.append(line)
    return described


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate tool contracts against the meta-schema.")
    parser.add_argument("paths", nargs="*", help="Specific contract files. Defaults to all approved contracts.")
    parser.add_argument("--dir", help="Validate every .json under this directory instead.")
    parser.add_argument(
        "--expect-invalid",
        action="store_true",
        help="Invert the exit code: pass only if every file is rejected. Used by the rejection demo.",
    )
    args = parser.parse_args()

    validator = Draft202012Validator(load_schema())
    files = discover(args.paths, args.dir)

    if not files:
        print("No contracts found to validate.")
        return 0

    failures = 0
    for path in files:
        rel = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
        problems = validate_file(path, validator)
        if problems:
            failures += 1
            print(f"\nREJECTED  {rel}")
            for problem in problems:
                print(f"    - {problem}")
        else:
            print(f"OK        {rel}")

    print()
    if args.expect_invalid:
        if failures == len(files):
            print(f"All {len(files)} fixture(s) rejected, as expected.")
            return 0
        print(f"Expected every fixture to be rejected, but {len(files) - failures} passed.")
        return 1

    if failures:
        print(f"{failures} of {len(files)} contract(s) failed structural validation.")
        return 1
    print(f"All {len(files)} contract(s) passed structural validation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

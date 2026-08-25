"""Evolution gate: a contract that changed must say so in its version.

Adding a contract is the easy case -- nothing depends on it yet. CHANGING one
that already merged is where the damage lives, and until this check existed
nothing in the gate looked at what a contract used to be. An argument could be
deleted from an approved tool with the version left untouched, and every other
check would pass: each one asks whether the contract is well formed *now*.

Two failures that costs, both silent:

  * A registry publishes `list_orders@3.1.0` twice with different content. The
    engine keys generated code on content rather than on the number, so it
    recovers -- but a human reading a version, or anything pinning by one, is
    quietly served something else.
  * An argument disappears and the version moves a patch. An agent that learned
    the tool's shape keeps sending the argument it was told about, the request
    drops it, and the answer comes back looking fine.

So the rule the README always stated is enforced here: patch for wording, minor
for what is added, major for what is taken away or changed underfoot.

Usage:
    python scripts/check_evolution.py                      # against origin/main
    python scripts/check_evolution.py --baseline <ref>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = REPO_ROOT / "contracts"

# Ordered, so "is this bump big enough" is a comparison rather than a table.
LEVELS = ("none", "patch", "minor", "major")


def _rank(level: str) -> int:
    return LEVELS.index(level)


def _version(contract: dict) -> tuple[int, int, int]:
    parts = str(contract.get("contractVersion", "0.0.0")).split(".")
    try:
        return tuple(int(p) for p in parts)  # type: ignore[return-value]
    except ValueError:
        return (0, 0, 0)


def bump_level(old: dict, new: dict) -> str:
    """How far the version actually moved."""
    before, after = _version(old), _version(new)
    if after == before:
        return "none"
    if after[0] != before[0]:
        return "major"
    if after[1] != before[1]:
        return "minor"
    return "patch"


def _arguments(contract: dict) -> dict[str, Any]:
    return ((contract.get("interface") or {}).get("input") or {}).get("schema", {}).get(
        "properties"
    ) or {}


def _required(contract: dict) -> set[str]:
    return set(
        ((contract.get("interface") or {}).get("input") or {}).get("schema", {}).get("required")
        or []
    )


def _response_fields(contract: dict) -> dict[str, Any]:
    return ((contract.get("interface") or {}).get("response") or {}).get("schema", {}).get(
        "properties"
    ) or {}


def _http(contract: dict) -> dict[str, Any]:
    binding = contract.get("binding") or {}
    return binding.get("http") or {} if binding.get("type") == "http" else {}


def _call_shape(contract: dict) -> dict[str, Any]:
    """The parts of a contract that decide what request goes out."""
    http = _http(contract)
    return {
        "type": (contract.get("binding") or {}).get("type"),
        "api": http.get("api"),
        "method": http.get("method"),
        "path": http.get("path"),
        "handler": (contract.get("binding") or {}).get("handler"),
    }


def required_level(old: dict, new: dict) -> tuple[str, list[str]]:
    """The smallest honest bump for this change, and why.

    Deliberately conservative about MAJOR. Every rule here describes something a
    caller already relies on: an argument it sends, a field it reads, the scopes
    a merchant consented to, or whether the tool runs when asked. Anything that
    only ADDS is minor, and anything that changes wording is patch.
    """
    reasons: list[str] = []

    if old == new:
        return "none", reasons

    old_args, new_args = _arguments(old), _arguments(new)

    for name in sorted(set(old_args) - set(new_args)):
        reasons.append(
            f"argument {name!r} was removed -- an agent told about it will keep sending it, "
            f"and the request will drop it silently"
        )

    newly_required = sorted(_required(new) - _required(old))
    for name in newly_required:
        reasons.append(
            f"argument {name!r} became required -- every existing caller that omits it now fails"
        )

    for name in sorted(set(old_args) & set(new_args)):
        before, after = old_args[name], new_args[name]
        old_enum, new_enum = before.get("enum"), after.get("enum")
        if old_enum and new_enum and (lost := sorted(set(old_enum) - set(new_enum))):
            reasons.append(f"argument {name!r} no longer accepts {', '.join(map(repr, lost))}")
        if before.get("type") != after.get("type"):
            reasons.append(
                f"argument {name!r} changed type from {before.get('type')!r} to {after.get('type')!r}"
            )

    for name in sorted(set(_response_fields(old)) - set(_response_fields(new))):
        reasons.append(
            f"response field {name!r} was removed -- anything reading it now reads nothing"
        )

    before_call, after_call = _call_shape(old), _call_shape(new)
    if before_call != after_call:
        changed = [k for k in before_call if before_call[k] != after_call[k]]
        reasons.append(
            f"the call itself changed ({', '.join(changed)}): "
            f"{before_call} -> {after_call}"
        )

    old_scopes = set(_http(old).get("auth", {}).get("scopes") or [])
    new_scopes = set(_http(new).get("auth", {}).get("scopes") or [])
    if gained := sorted(new_scopes - old_scopes):
        reasons.append(
            f"scope {', '.join(map(repr, gained))} was added -- scopes are granted per app "
            f"installation, so every store has to consent again"
        )
    if lost := sorted(old_scopes - new_scopes):
        reasons.append(f"scope {', '.join(map(repr, lost))} was dropped")

    old_exec = (old.get("governance") or {}).get("execution") or {}
    new_exec = (new.get("governance") or {}).get("execution") or {}
    if old_exec != new_exec:
        reasons.append(
            f"execution policy changed ({old_exec} -> {new_exec}) -- a caller that ran "
            f"directly may now be waiting for a human, or the reverse"
        )

    if reasons:
        return "major", reasons

    # Additive: nothing above was taken away, so what is left is what appeared.
    if added := sorted(set(new_args) - set(old_args)):
        reasons.append(f"optional argument(s) added: {', '.join(added)}")
    if added := sorted(set(_response_fields(new)) - set(_response_fields(old))):
        reasons.append(f"response field(s) added: {', '.join(added)}")
    if reasons:
        return "minor", reasons

    return "patch", ["descriptions, hints, rendering or policy detail changed"]


def problems_for(name: str, old: dict, new: dict) -> list[str]:
    """What is wrong with how this contract's version moved, if anything."""
    needed, reasons = required_level(old, new)
    if needed == "none":
        return []

    actual = bump_level(old, new)
    detail = "".join(f"\n        - {r}" for r in reasons)
    old_version = old.get("contractVersion")
    new_version = new.get("contractVersion")

    if _version(new) < _version(old):
        return [
            f"{name}: contractVersion went backwards, {old_version} -> {new_version}"
        ]

    if actual == "none":
        return [
            f"{name}: the contract changed but contractVersion is still {old_version!r}. "
            f"This needs a {needed} bump:{detail}"
        ]

    if _rank(actual) < _rank(needed):
        return [
            f"{name}: {old_version} -> {new_version} is a {actual} bump, but this change "
            f"needs {needed}:{detail}"
        ]

    return []


# -- the baseline -----------------------------------------------------------


def _git(*args: str) -> tuple[int, str]:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    return result.returncode, result.stdout


def resolve_baseline(preferred: str) -> str | None:
    """A ref to compare against, or None when this checkout has no history to use.

    Tried in order rather than demanded: a contributor running the gate locally
    on a fresh clone may have no `origin/main`, and refusing to run is worse than
    saying so.
    """
    for ref in (preferred, "origin/main", "main"):
        if ref and _git("rev-parse", "--verify", "--quiet", ref)[0] == 0:
            return ref
    return None


def baseline_contract(ref: str, relative: str) -> dict | None:
    """The approved version of one contract, or None if it is new."""
    code, text = _git("show", f"{ref}:{relative}")
    if code != 0:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Check how approved contracts changed.")
    parser.add_argument(
        "--baseline",
        default="origin/main",
        help="Git ref holding the approved contracts (default: origin/main).",
    )
    args = parser.parse_args()

    ref = resolve_baseline(args.baseline)
    if ref is None:
        print(
            f"No baseline to compare against ({args.baseline!r} does not resolve), so no "
            f"contract could have changed relative to one. Nothing checked."
        )
        return 0

    failures: list[str] = []
    checked = updated = 0

    for path in sorted(CONTRACTS_DIR.rglob("*.json")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        try:
            new = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue  # validate_contracts.py reports this properly

        old = baseline_contract(ref, relative)
        if old is None:
            continue  # a new contract has nothing to have changed from

        checked += 1
        name = (new.get("interface") or {}).get("name") or path.stem
        if problems := problems_for(name, old, new):
            failures.extend(problems)
        elif old != new:
            updated += 1
            print(f"OK        {name}  {old.get('contractVersion')} -> {new.get('contractVersion')}")

    # A contract that vanished is a tool the engine is still serving.
    code, listing = _git("ls-tree", "--name-only", ref, "contracts/")
    if code == 0:
        for relative in listing.split():
            if relative.endswith(".json") and not (REPO_ROOT / relative).exists():
                failures.append(
                    f"{Path(relative).stem}: this approved contract was deleted. Removing it "
                    f"retires a tool the engine is serving and any agent already routing to it"
                )

    if failures:
        print(f"\nREJECTED  against {ref}")
        for problem in failures:
            print(f"    - {problem}")
        print(
            "\nBump rules: patch for wording and rendering, minor for what is added, "
            "major for what is removed or changed underfoot."
        )
        return 1

    print(
        f"\n{checked} approved contract(s) compared against {ref}; "
        f"{updated} changed, every one with an honest version."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

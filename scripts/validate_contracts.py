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
import re
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


# JSON Schema cannot carry custom error messages, so a cross-field rule surfaces as
# something like "'coupons.read_write' does not match '\\.read$'" -- technically exact
# and useless to the partner who has to fix it. The helpers below turn the rules that
# matter into what actually went wrong and what to do about it.
#
# They key off the validator and its declared value rather than the rendered message:
# the message embeds the regex, so a scope literally named "coupons.write" would match
# a naive search for "read_write" against the pattern text and earn a confidently wrong
# explanation.

SCOPE_FORMAT_PATTERN = "^[a-z][a-z0-9_]*\\.(read|read_write)$"
READ_ONLY_SCOPE_PATTERN = "\\.read$"


def _http_method(contract: dict) -> str | None:
    if not isinstance(contract, dict):
        return None
    binding = contract.get("binding") or {}
    if binding.get("type") != "http":
        return None
    return (binding.get("http") or {}).get("method")


def _hint_for(error, location: str, message: str, contract: dict) -> str | None:
    """Explain a cross-field rule in terms the author can act on.

    Two of these rules can produce the *same* JSON Schema error from different
    causes -- readOnly is demanded both by "GET only reads" and by "only reads may
    be cached". Guessing between them prints a confident, wrong instruction, so the
    method is resolved from the contract before choosing.
    """
    if location == "kind" and "single-tool" in message:
        return (
            "only single-tool contracts exist today: one endpoint per file. "
            "Multi-tool packages and openapi-import are future work -- see the README"
        )

    if "auth/scopes" in location:
        if error.validator == "contains":
            return (
                "a tool that writes needs a .read_write scope; the upstream rejects a "
                "write attempted with a read-only credential"
            )
        if error.validator == "pattern":
            if error.validator_value == READ_ONLY_SCOPE_PATTERN:
                return (
                    "least privilege: a read-only tool must not request a read_write scope. "
                    "Use the .read scope, or set interface.annotations.readOnlyHint to false if it really writes"
                )
            if error.validator_value == SCOPE_FORMAT_PATTERN:
                return (
                    "scopes follow the <domain>.read / <domain>.read_write convention, "
                    "e.g. coupons.read"
                )

    if location.endswith("annotations/readOnlyHint"):
        method = _http_method(contract)
        if "True was expected" in message:
            if method == "GET":
                return (
                    "GET only reads, so mark it read-only -- otherwise it is never cached "
                    "and the agent is told it has side effects"
                )
            return (
                "only a read-only tool may be cacheable; either set caching.cacheable to "
                "false, or mark this read-only if it genuinely does not change anything"
            )
        if "False was expected" in message:
            return f"{method or 'this method'} changes store data, so it cannot be read-only"

    if location.endswith("annotations/destructiveHint"):
        if "True was expected" in message:
            return "DELETE removes real data; it is destructive regardless of intent"
        if "False was expected" in message:
            return "a read-only tool cannot also be destructive"

    if location.endswith("caching/cacheable") and "False was expected" in message:
        return (
            "destructive tools are never cacheable -- a cached write serves a stale result "
            "as though the write had happened"
        )

    if location.endswith("execution/humanApproval") and "required" in message:
        return (
            "this tool needs a human in the loop: execution.mode propose-apply with "
            "humanApproval required"
        )

    if location.endswith("caching") and "ttlSeconds" in message:
        return "a cacheable result with no ttlSeconds never expires"

    if location.endswith("response/collection") and "True was expected" in message:
        return "a paginated response returns an array, so collection must be true"

    return None


def _location(error) -> str:
    return "/".join(str(p) for p in error.absolute_path) or "(root)"


def _line(error, contract: dict) -> str:
    """One reported problem, with a plain-language hint when we have one."""
    location, message = _location(error), error.message
    hint = _hint_for(error, location, message, contract)
    return f"{location}: {message}" + (f"\n        -> {hint}" if hint else "")


def _describe(error, contract: dict) -> list[str]:
    """Turn a jsonschema error into lines a contributor can act on.

    The one unreadable case left is the binding's `oneOf` (http vs builtin), where
    jsonschema reports every branch's complaints at once; the describer picks the
    branch the declared `type` actually meant.
    """
    if error.validator == "oneOf" and error.context:
        return _describe_binding_oneof(error, contract)
    return [_line(error, contract)]


def _describe_binding_oneof(error, contract: dict) -> list[str]:
    """`oneOf`s here are discriminated on a `type` const (http vs none).

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
        return [_line(error, contract)]
    return [_line(sub, contract) for sub in sorted(matching, key=lambda e: list(e.absolute_path))]


PLACEHOLDER = re.compile(r"\{([^}]+)\}")


def consistency_problems(contract: dict) -> list[str]:
    """Cross-references JSON Schema cannot express.

    A schema can say `parameters.path` is an array of objects. It cannot say that
    the array must line up with the {placeholders} in `path`, or that every `from`
    must name an argument the tool actually accepts. Those mismatches produce a
    contract that validates perfectly and then builds a broken request -- a URL
    with a literal {coupon_id} in it, or a filter silently dropped because it reads
    an argument nobody can pass.
    """
    problems: list[str] = []
    body = contract

    binding = body.get("binding") or {}
    if binding.get("type") != "http":
        return problems
    http = binding.get("http") or {}
    params = http.get("parameters") or {}

    arguments = set(body.get("interface", {}).get("input", {}).get("schema", {}).get("properties") or {})
    required_arguments = set(
        body.get("interface", {}).get("input", {}).get("schema", {}).get("required") or []
    )

    # 1. Path placeholders and their mappings must agree, in both directions.
    placeholders = set(PLACEHOLDER.findall(http.get("path", "")))
    mapped_path = {p.get("name") for p in (params.get("path") or [])}
    for missing in sorted(placeholders - mapped_path):
        problems.append(
            f"path contains {{{missing}}} but parameters.path has no entry for it, "
            f"so the request URL would keep the literal placeholder"
        )
    for extra in sorted(mapped_path - placeholders):
        problems.append(
            f"parameters.path maps {extra!r}, which does not appear in path "
            f"{http.get('path')!r}"
        )

    # 2. Every mapping must read an argument the tool actually accepts.
    consumed: set[str] = set()
    for section, entries in (("path", params.get("path")), ("query", params.get("query"))):
        for entry in entries or []:
            if "constant" in entry:
                continue
            source = entry.get("from") or entry.get("name")
            consumed.add(source)
            if source not in arguments:
                problems.append(
                    f"parameters.{section} entry {entry.get('name')!r} reads argument "
                    f"{source!r}, which is not declared in interface.input.schema.properties"
                )

    body_spec = params.get("body") or {}
    if body_spec.get("mode") == "mapped":
        for entry in body_spec.get("fields") or []:
            source = entry.get("from") or entry.get("name")
            consumed.add(source)
            if source not in arguments:
                problems.append(
                    f"parameters.body field {entry.get('name')!r} reads argument "
                    f"{source!r}, which is not declared in interface.input.schema.properties"
                )
    elif body_spec.get("mode") == "passthrough":
        consumed |= arguments  # everything left over becomes the body

    # 3. An argument the caller must supply, that the request never uses, is a
    #    promise the tool cannot keep.
    for orphan in sorted(required_arguments - consumed):
        problems.append(
            f"argument {orphan!r} is required but never used in the path, query "
            f"or body, so supplying it would change nothing"
        )

    # 4. Cache keys and validation rules must name real arguments.
    for key in body.get("governance", {}).get("caching", {}).get("keyBy") or []:
        if key not in arguments:
            problems.append(
                f"caching.keyBy names {key!r}, which is not one of the tool's arguments"
            )
    for rule in body.get("validation", {}).get("rules") or []:
        if rule.get("field") not in arguments:
            problems.append(
                f"validation rule targets {rule.get('field')!r}, which is not one of "
                f"the tool's arguments, so the guard would never run"
            )

    return problems


def validate_file(path: Path, validator: Draft202012Validator) -> list[str]:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"(root): not valid JSON -- {exc}"]

    errors = sorted(validator.iter_errors(contract), key=lambda e: list(e.absolute_path))
    # Deduplicate: oneOf branches often report the same underlying problem twice.
    seen, described = set(), []
    for error in errors:
        for line in _describe(error, contract):
            if line not in seen:
                seen.add(line)
                described.append(line)

    # Only once the shape is valid. Running these against a contract the schema has
    # already rejected produces cascading noise about fields that are simply absent.
    if not described:
        described = consistency_problems(contract)

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

"""Semantic gate: LLM-as-judge review of contract quality.

The structural check (validate_contracts.py) proves a contract is well-formed.
It cannot tell you the description is boilerplate, the whenToUse hints are
useless, or the tool duplicates one already in the registry. That is this file.

Two paths, same verdict shape:
  * OPENAI_API_KEY set   -> an OpenAI model judges each contract.
  * no key               -> deterministic heuristics.

The fallback is not a stub. It exists so the gate is demonstrable offline and so
a fork without repo secrets still gets a real signal on its PRs -- a check that
silently passes when a key is missing is worse than no check.

Usage:
    python scripts/eval_contracts.py                     # all approved contracts
    python scripts/eval_contracts.py path/a.json ...     # specific files
    python scripts/eval_contracts.py --dir tests/fixtures/invalid --expect-fail
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]

# .env.example documents OPENAI_API_KEY for local runs, so a local run has to read
# it. Without this the key sits in .env doing nothing and every local review
# silently falls back to heuristics -- so the gate a contributor sees locally is
# not the gate CI runs.
load_dotenv(REPO_ROOT / ".env")
CONTRACTS_DIR = REPO_ROOT / "contracts"

# Override with CM_JUDGE_MODEL when OpenAI ships a better fit; a bad model id
# degrades to the heuristic path rather than breaking the gate.
MODEL = os.environ.get("CM_JUDGE_MODEL", "gpt-5.1")

# Phrases that show up when someone fills the template without thinking.
BOILERPLATE = ("todo", "tbd", "lorem ipsum", "description here", "xxx", "fixme")

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "reasons": {
            "type": "array",
            "items": {"type": "string"},
            "description": "One sentence per issue. Empty when the verdict is pass.",
        },
        "notes": {
            "type": "string",
            "description": "One short sentence of overall assessment.",
        },
    },
    "required": ["verdict", "reasons", "notes"],
    "additionalProperties": False,
}

JUDGE_SYSTEM = """You review tool contracts submitted to a registry that turns
upstream REST endpoints -- primarily the Salla Admin API -- into MCP tools for
merchant-facing agents.

An agent picks tools using only the contract's `description`, `whenToUse`, and
`whenNotToUse`. A contract that is structurally valid but semantically vague makes
the agent pick the wrong tool against a real merchant's store, which is the
failure mode you exist to catch. The structural gate has already checked shape,
scopes format, and the method/governance cross-rules -- do not re-litigate those.

Fail a contract when any of these hold:
- The description is boilerplate, or too vague to route on.
- whenToUse restates the description instead of naming concrete situations a
  merchant would actually raise.
- whenNotToUse misses the obvious confusion with a sibling tool (list vs detail,
  read vs write) or fails to name the sibling.
- The tool duplicates an existing registry entry without a clear distinction.
- The binding does not match what the interface claims: a path that plainly
  belongs to a different endpoint than the description promises, or a response
  schema describing fields the upstream does not return.
- The input surface is careless: arguments exposed that an agent should never
  control (per_page in the thousands, raw status ids with no guard), or a
  destructive tool whose hints would let it trigger on vague vocabulary.
- Anything unsafe for the merchant: over-broad scopes for the job, a write
  reachable without explicit intent, response fields that leak data the tool has
  no business returning.

Conventions of this registry, so you do not fail a contract for following them:
- `interface.response.schema` describes ONE record. When
  `binding.http.response.collection` is true the engine returns
  {items: [...], count, pagination} and validates each item against that schema,
  so a single-object schema beside collection: true is correct, not a mismatch.
- The envelope is never modelled in a contract. The engine unwraps `dataPath` and
  attaches `pagination` itself; a contract that described {status, success, data}
  would be wrong.
- No contract carries a base URL, a host, or a credential. The engine owns those
  per upstream. Their absence is the design, not an omission.

Judge only what is in front of you. Do not fail a contract for style, naming
preferences, or a missing optional field. Be specific in every reason: name the
field and say what is wrong with it."""


@dataclass
class Verdict:
    path: Path
    verdict: str
    reasons: list[str] = field(default_factory=list)
    notes: str = ""
    source: str = "heuristic"

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"


def discover(paths: list[str] | None, directory: str | None) -> list[Path]:
    if paths:
        return [Path(p).resolve() for p in paths]
    root = Path(directory).resolve() if directory else CONTRACTS_DIR
    return sorted(root.rglob("*.json"))


def registry_names(exclude: Path) -> set[str]:
    """Tool names already approved, so the judge can spot duplicates."""
    names: set[str] = set()
    for path in CONTRACTS_DIR.rglob("*.json"):
        if path.resolve() == exclude.resolve():
            continue
        try:
            contract = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        name = contract.get("interface", {}).get("name")
        if name:
            names.add(name)
    return names


# --------------------------------------------------------------------------
# Heuristic path -- used when no API key is available.
# --------------------------------------------------------------------------


def judge_heuristically(path: Path, contract: dict) -> Verdict:
    reasons: list[str] = []
    existing = registry_names(exclude=path)

    interface = contract.get("interface", {})
    name = interface.get("name", "(unnamed)")
    description = (interface.get("description") or "").strip()
    governance = contract.get("governance", {})
    annotations = interface.get("annotations", {})
    caching = governance.get("caching", {})
    binding = contract.get("binding", {})

    lowered = description.lower()
    if any(marker in lowered for marker in BOILERPLATE):
        reasons.append(f"{name}: description still contains template boilerplate.")
    if len(description.split()) < 6:
        reasons.append(f"{name}: description is too short to route on.")

    when_to_use = interface.get("whenToUse") or []
    if not when_to_use:
        reasons.append(f"{name}: no whenToUse hints, so the agent cannot route to it.")
    for hint in when_to_use:
        if hint.strip().lower() == lowered:
            reasons.append(f"{name}: whenToUse just restates the description.")
    if not interface.get("whenNotToUse"):
        reasons.append(
            f"{name}: no whenNotToUse hints -- say when the agent should pick something else."
        )

    if name in existing:
        reasons.append(f"{name}: a tool with this name is already in the registry.")

    # Quality questions the structural gate cannot ask. Hard contradictions
    # (method vs readOnly, destructive vs cacheable) are the schema's job;
    # what belongs here is judgment about whether the contract is GOOD.
    http = binding.get("http") or {}

    if http:
        # Salla-style upstreams paginate lists with `page`; a collection endpoint
        # that hides it gives the agent only the first page with no way to ask
        # for more.
        response = http.get("response") or {}
        if response.get("pagination") == "standard":
            query_names = {
                q.get("name") for q in (http.get("parameters") or {}).get("query") or []
            }
            if "page" not in query_names:
                reasons.append(
                    f"{name}: the response is paginated but the tool does not expose a "
                    f"`page` query parameter, so the agent can only ever see page one."
                )

        # A tool that removes or rewrites data, whose whenToUse does not demand
        # explicit intent, invites the router to pick it on vague vocabulary
        # overlap with its read-only siblings.
        if annotations.get("destructiveHint") or http.get("method") == "DELETE":
            intent_words = ("explicit", "asks to", "confirms", "requested")
            hints_text = " ".join(when_to_use).lower()
            if not any(word in hints_text for word in intent_words):
                reasons.append(
                    f"{name}: destructive, but no whenToUse hint requires the merchant "
                    f"to have explicitly asked. Say 'the merchant explicitly asks to ...'"
                )

    if caching.get("cacheable") and not caching.get("ttlSeconds"):
        reasons.append(f"{name}: cacheable but no ttlSeconds, so entries never expire.")

    return Verdict(
        path=path,
        verdict="fail" if reasons else "pass",
        reasons=reasons,
        notes="Heuristic review (no OPENAI_API_KEY set); no LLM judgment applied.",
        source="heuristic",
    )


# --------------------------------------------------------------------------
# LLM path.
# --------------------------------------------------------------------------


def judge_with_openai(path: Path, contract: dict) -> Verdict:
    from openai import OpenAI

    client = OpenAI()
    existing = sorted(registry_names(exclude=path))

    prompt = (
        f"Tools already in the registry: {', '.join(existing) or '(none)'}\n\n"
        f"Contract under review ({path.name}):\n\n"
        f"```json\n{json.dumps(contract, indent=2)}\n```"
    )

    # strict json_schema output: the model must return exactly the verdict shape,
    # so there is no free-text parsing to get wrong.
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "contract_verdict",
                "strict": True,
                "schema": VERDICT_SCHEMA,
            },
        },
    )

    message = response.choices[0].message
    if getattr(message, "refusal", None):
        return Verdict(
            path=path,
            verdict="fail",
            reasons=["The judge declined to review this contract."],
            notes="Model refusal -- review this submission by hand.",
            source="openai",
        )

    payload = json.loads(message.content)
    return Verdict(
        path=path,
        verdict=payload["verdict"],
        reasons=payload.get("reasons", []),
        notes=payload.get("notes", ""),
        source="openai",
    )


def judge(path: Path, use_llm: bool) -> Verdict:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return Verdict(path=path, verdict="fail", reasons=[f"not valid JSON -- {exc}"])

    if not use_llm:
        return judge_heuristically(path, contract)

    try:
        return judge_with_openai(path, contract)
    except Exception as exc:  # noqa: BLE001 - the gate must not vanish on an API hiccup
        fallback = judge_heuristically(path, contract)
        fallback.notes = f"LLM judge unavailable ({type(exc).__name__}); fell back to heuristics."
        return fallback


def write_summary(verdicts: list[Verdict], source: str) -> None:
    """Emit a markdown table to the PR's check summary when running in Actions."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    lines = [f"## Semantic contract review ({source})", "", "| Contract | Verdict |", "|---|---|"]
    for v in verdicts:
        rel = v.path.relative_to(REPO_ROOT) if v.path.is_relative_to(REPO_ROOT) else v.path
        lines.append(f"| `{rel}` | {'PASS' if v.passed else 'FAIL'} |")

    for v in verdicts:
        if v.passed:
            continue
        rel = v.path.relative_to(REPO_ROOT) if v.path.is_relative_to(REPO_ROOT) else v.path
        lines += ["", f"### `{rel}`", ""] + [f"- {r}" for r in v.reasons]
        if v.notes:
            lines += ["", f"_{v.notes}_"]

    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM-as-judge review of tool contracts.")
    parser.add_argument("paths", nargs="*", help="Specific contract files.")
    parser.add_argument("--dir", help="Review every .json under this directory instead.")
    parser.add_argument(
        "--expect-fail",
        action="store_true",
        help="Invert the exit code: pass only if every contract is rejected.",
    )
    parser.add_argument(
        "--no-llm", action="store_true", help="Force the heuristic path even if a key is set."
    )
    args = parser.parse_args()

    use_llm = bool(os.environ.get("OPENAI_API_KEY")) and not args.no_llm
    source = f"OpenAI {MODEL}" if use_llm else "deterministic heuristics"
    print(f"Semantic review via {source}.\n")

    files = discover(args.paths, args.dir)
    if not files:
        print("No contracts found to review.")
        return 0

    verdicts = [judge(path, use_llm) for path in files]

    for v in verdicts:
        rel = v.path.relative_to(REPO_ROOT) if v.path.is_relative_to(REPO_ROOT) else v.path
        if v.passed:
            print(f"PASS      {rel}")
        else:
            print(f"\nFAIL      {rel}")
            for reason in v.reasons:
                print(f"    - {reason}")
            if v.notes:
                print(f"    ({v.notes})")

    write_summary(verdicts, source)

    failures = sum(1 for v in verdicts if not v.passed)
    print()
    if args.expect_fail:
        if failures == len(verdicts):
            print(f"All {len(verdicts)} contract(s) rejected, as expected.")
            return 0
        print(f"Expected every contract to be rejected, but {len(verdicts) - failures} passed.")
        return 1

    if failures:
        print(f"{failures} of {len(verdicts)} contract(s) failed semantic review.")
        return 1
    print(f"All {len(verdicts)} contract(s) passed semantic review.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

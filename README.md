# cm_mcp_contracts

**The rulebook, the templates, the approved contracts, and the gate that guards them.**

This repository is the traffic lane. Partners add tools by opening a pull request here — not by
writing server code anywhere. On merge, CI publishes a registry artifact that
[`cm_mcp_engine`](../cm_mcp_engine) downloads, verifies it can execute, and serves as MCP tools.

```
schema/       tool-contract.v1.json   the one rulebook, owner-only
templates/    copy-and-fill stubs     one per contract kind
contracts/    the approved lane       partners submit here
scripts/      the gate + the builder
```

## Add a tool

1. Copy a template into `contracts/` and fill it in. The `$schema` key means your editor
   validates as you type, before CI ever sees it.
2. Open a PR. `contract-gate.yml` runs three jobs:
   - **structural** — every contract against the meta-schema, plus a check that the deliberately
     broken fixtures are *still* rejected (the gate guarding itself).
   - **semantic** — an LLM-as-judge on description quality, routing hints, duplication, and
     governance contradictions. Falls back to deterministic heuristics with no API key, so fork
     PRs get a real signal instead of a silent pass.
   - **buildable** — the registry actually builds, catching duplicate tool names before merge.
3. Get a CODEOWNERS review and merge. Merge is approval.

```bash
uv sync --extra dev
uv run pytest                                 # 31 tests
uv run python scripts/validate_contracts.py   # structural
uv run python scripts/eval_contracts.py       # semantic
uv run python scripts/build_registry.py       # -> dist/registry.generated.json
pwsh scripts/demo_gate.ps1                    # watch a bad contract get rejected
```

## Contract anatomy

Two deliberately separate layers, plus where it calls:

- **`interface`** — *what the tool is*. Maps 1:1 to the standard MCP tool shape, plus
  `whenToUse` / `whenNotToUse` selection hints the agent routes on.
- **`governance`** — *how it is allowed to run*. Annotations, execution mode, caching policy.
- **`binding`** — `http` for a real API, or `none` with a `builtin://` handler for a pure function
  the engine runs in-process.

The meta-schema enforces cross-field rules that are correctness bugs rather than preferences: a
destructive tool can never be cacheable, a read-only tool can never be destructive, and
`propose-apply` always requires human approval. `_comment` keys are permitted anywhere, so the
hints in a template survive into a real submission.

## What this repo cannot check

**Whether the engine can actually run your contract.** `builtin://forecast_weather` satisfies every
schema rule and is unrunnable if no such handler exists in the engine. That question belongs to
the engine and is answered there, by `consume-registry.yml`, before any registry is pinned. A
contract can therefore merge here and still be rejected downstream — check the engine's
consume-registry run if a tool never appears.

## Publishing

`publish-registry.yml` runs on merge to `main`:

1. builds and re-validates `registry.generated.json`
2. publishes an immutable `registry-<run>-<sha>` release **and** moves the rolling
   `registry-latest` pointer
3. fires a `repository_dispatch` at `cm_mcp_engine`

**Setup required:** the dispatch needs a fine-grained PAT with *Contents: read and write* on
`cm_mcp_engine`, stored as the `ENGINE_DISPATCH_TOKEN` secret — `GITHUB_TOKEN` cannot reach another
repository. Without it publishing still succeeds and the engine picks the registry up on its daily
scheduled run instead.

Also set: `ANTHROPIC_API_KEY` (optional, upgrades the semantic gate), real handles in
[CODEOWNERS](CODEOWNERS), and branch protection requiring `structural`, `semantic`, `buildable`,
plus a CODEOWNERS review on `contracts/**`.

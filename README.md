# cm_mcp_contracts

**The rulebook, the templates, the approved contracts, and the gate that guards them.**

This repository turns **Salla Admin API endpoints** into agent-usable MCP tools — by contract, not
by code. A contributor describes one endpoint in a JSON contract; the gate reviews it; on merge it
is published to a registry that [`cm_mcp_engine`](../cm_mcp_engine) downloads, verifies it can
execute, and serves as MCP tools acting on a merchant's store.

```
schema/       tool-contract.v1.json   the one rulebook, Salla-native, owner-only
templates/    copy-and-fill stubs     one per contract kind
contracts/    the approved lane       submissions land here
scripts/      the gate + the builder
```

## What a contract declares

Each contract wraps **one Salla endpoint** (or a package of related ones) in three layers:

- **`interface`** — the MCP-facing surface: name, description, `whenToUse`/`whenNotToUse` routing
  hints, the tool's input schema, and the **unwrapped** response shape. The tool's arguments are
  deliberately *narrower* than Salla's — a list endpoint with twenty filters exposes the three an
  agent should actually set.
- **`binding.salla`** — the endpoint itself: Salla's `operationId`, method, path, `docsUrl` for
  reviewer traceability, required OAuth **scopes**, how tool arguments map onto path/query/body,
  and how to unwrap Salla's `{status, success, data}` envelope (plus `pagination` on lists).
  There is **no base URL** — the engine owns it, so a contract cannot pin production.
- **`governance`** — annotations, execution mode, caching policy. Cross-checked against the
  binding by the schema itself.

## Salla rules the schema enforces

These are rejections, not warnings — each catches a contract that is well-formed and still wrong:

| Rule | Why it matters |
|---|---|
| GET must be `readOnly: true` | otherwise it is never cached and the agent is told it has side effects |
| POST/PUT/PATCH/DELETE cannot be read-only | a write marked read-only would be cached — a stale result served as though the write happened |
| DELETE must be `destructive` + `humanApproval: required` | removing merchant data always gets a human in the loop |
| a read-only tool may only ask for `.read` scopes | least privilege: Salla grants scopes per app install, so an over-broad ask affects every merchant |
| a writing tool must hold a `.read_write` scope | Salla would 401 the call at runtime; reject it at review time |
| only read-only tools may be cacheable, and cacheable requires `ttlSeconds` | a write is never cached; a cache with no TTL never expires |
| `pagination: standard` implies `collection: true` | a paginated Salla response is an array by definition |

Beyond the schema, `validate_contracts.py` cross-references what JSON Schema cannot: every
`{placeholder}` in the path has a mapping, every mapping reads a declared argument, every required
argument actually reaches the request, and `keyBy`/validation rules name real arguments. Rejections
come with plain-language hints:

```
- binding/salla/auth/scopes/0: 'coupons.read_write' does not match '\.read$'
    -> least privilege: a read-only tool must not request a read_write scope. Use the
       .read scope, or set annotations.readOnly to false if it really writes
```

## Add a tool

1. Open the endpoint on **docs.salla.dev**. Everything in the binding — operationId, method,
   path, scopes — is copied from there, not invented.
2. Copy a template from `templates/` into `contracts/` and fill it. The `$schema` key gives you
   editor validation as you type.
3. Open a PR. `contract-gate.yml` runs three jobs: **structural** (schema + cross-reference
   checks, with the rejection fixtures re-verified so the gate guards itself), **semantic**
   (LLM-as-judge on routing-hint quality, sibling confusion, careless input surfaces; deterministic
   heuristics when no API key is present), and **buildable** (the registry actually builds).
4. CODEOWNERS review, merge. **Merge is approval** — `publish-registry.yml` builds the artifact,
   publishes immutable + rolling releases, and notifies the engine.

## What this repo cannot check

**Whether the engine can execute the contract.** That question belongs to
[`cm_mcp_engine`](../cm_mcp_engine), which verifies every published registry before pinning it —
including that it implements the `salla` binding features a contract uses. A contract can merge
here and still be rejected downstream; check the engine's consume-registry run if a tool never
appears.

**Whether Salla's API actually behaves as documented.** Contracts are written from
docs.salla.dev. The engine's integration against a real store is where reality is tested.

## Local commands

```bash
uv sync --extra dev
uv run pytest                                 # 59 tests
uv run python scripts/validate_contracts.py   # structural + cross-reference
uv run python scripts/eval_contracts.py       # semantic (heuristics without a key)
uv run python scripts/build_registry.py       # -> dist/registry.generated.json
pwsh scripts/demo_gate.ps1                    # watch bad contracts get rejected
```

## Repo setup

Secrets: `ANTHROPIC_API_KEY` (optional, upgrades the semantic gate), `ENGINE_DISPATCH_TOKEN`
(fine-grained PAT with *Contents: read and write* on `cm_mcp_engine`; without it the engine falls
back to its daily scheduled pull). Replace `@owner`/`@reviewers` in [CODEOWNERS](CODEOWNERS), and
protect `main` requiring `structural`, `semantic`, `buildable` plus a CODEOWNERS review on
`contracts/**`.

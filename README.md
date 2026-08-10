# MCP Tool Contracts — the Code-Mode Registry

**Add an API capability to the agent platform by submitting one JSON file. No server code.**

This repository is the governed registry at the heart of the code-mode MCP platform. Every file in
`contracts/` is a **tool contract**: a declarative description of exactly one upstream REST
endpoint — what it is, where it calls, and how it is allowed to run. Once a contract passes the
gate and merges, the platform does the rest: it is published to a registry,
[`cm_mcp_engine`](https://github.com/jamalla/cm_mcp_engine) verifies it can execute it and turns it
into a runnable, sandboxed, cached MCP tool, and the routing agent in
[`cm_mcp_agent`](https://github.com/jamalla/cm_mcp_agent) starts offering it to users.

The contract format is **modeled on the [Salla Admin API](https://docs.salla.dev/)'s nature** —
enveloped `{status, success, data}` responses, scoped OAuth, standard pagination, Laravel-style
query arrays — and Salla is the platform's primary upstream. The format does **not** tie you to
Salla, though: `binding.http.api` selects which configured upstream the engine calls, and any
API following the same conventions fits.

```
you                          this repo                        downstream
────                         ─────────                        ──────────
write contract  ──PR──>  contract-gate (3 checks)
                         CODEOWNERS review
                         merge == approval  ──publish──>  registry release
                                                          cm_mcp_engine verifies + pins
                                                          agent starts routing to your tool
```

## Repository layout

| Path | What it is | Who touches it |
|---|---|---|
| `schema/tool-contract.v1.json` | the one rulebook every contract must satisfy | owners only |
| `templates/single-tool.template.json` | copy-and-fill starter | you copy it |
| `contracts/` | the approved lane — submissions land here | contributors, via PR |
| `scripts/` | the gate (structural + semantic) and the registry builder | owners only |
| `tests/fixtures/invalid/` | deliberately broken contracts that must stay rejected | owners only |

## Anatomy of a contract

One endpoint, one tool, one file. Three layers:

```jsonc
{
  "$schema": "../schema/tool-contract.v1.json",
  "contractVersion": "1.0.0",
  "kind": "single-tool",

  "interface":  { /* WHAT: name, title, description, whenToUse/whenNotToUse,
                     MCP annotations (readOnlyHint, destructiveHint, ...),
                     the tool's input schema, the unwrapped response + ui hint */ },

  "binding":    { /* WHERE: type "http" -> which upstream (api), method, path,
                     required scopes, argument->request mapping, envelope unwrapping.
                     Or type "none" -> a builtin:// pure function. */ },

  "dependencies": [ /* other contracts this tool relies on, with reasons */ ],

  "governance": { /* HOW: direct vs propose-apply execution, caching policy --
                     cross-checked against the annotations and the method */ }
}
```

- **`interface`** is the agent-facing surface. It maps 1:1 to the standard MCP tool declaration,
  plus the routing hints (`whenToUse` / `whenNotToUse`) an agent decides by.
- **`binding`** is the call. There is deliberately **no base URL and no secret** in a contract:
  the engine owns each upstream's host and resolves its credential at call time (for Salla, the
  installing merchant's OAuth token). You declare only the *scopes* that credential must carry.
- **`governance`** is the permission slip. The schema cross-checks it against the annotations and
  the binding, so the layers cannot quietly disagree.

### What every contract declares, and what it becomes

| You declare | Where in the contract | What it becomes |
|---|---|---|
| tool name | `interface.name` | the MCP tool's `name` |
| display title | `interface.title` | the MCP tool's `title` (and `annotations.title`) |
| one-line purpose | `interface.description` | the MCP tool's `description` |
| **core annotations** | `interface.annotations` — `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint` (MCP's exact names, all four required) | served verbatim as MCP `ToolAnnotations` — and cross-checked by the gate against the method and the caching policy |
| **LLM guidance** | `interface.whenToUse` / `whenNotToUse` — what the tool is for, and what it is *not* for (name the sibling) | folded into the tool description for any MCP client, and published under `_meta` for structured routing |
| arguments | `interface.input.schema` | the MCP tool's `inputSchema` |
| **UI display** | `interface.response.ui` — `card` / `table` / `text` / `json`, with `{field}` interpolation | the rendering hint a client uses instead of dumping raw JSON |
| **auth** | `binding.http.auth.scopes` | the scopes the engine's resolved credential must carry — never a secret, never a token |
| **dependencies** | `dependencies[]` — `{contract, reason}` per dependent tool | reviewer + agent documentation (the POC engine does not resolve them yet) |

## Submit a tool — step by step

> Delegating the submission to someone (or something) else? Hand them the ready-made briefing
> in [docs/contributor-prompt.md](docs/contributor-prompt.md) — a worked example for
> `list_categories`, adaptable to any endpoint by swapping three lines.

1. **Pick the endpoint.** For Salla, its docs page ([docs.salla.dev](https://docs.salla.dev/))
   gives you the method, path and scopes ready to copy — using them saves your reviewer
   guesswork, and `docsUrl` is the cheapest traceability you can offer.
2. **Copy the template**: `templates/single-tool.template.json` → `contracts/<tool_name>.json`.
3. **Fill it in**, following the conventions below. The `$schema` key gives you editor
   validation as you type, so most mistakes never reach CI.
4. **Check it locally** (optional but fast):
   ```bash
   uv sync --extra dev
   uv run python scripts/validate_contracts.py contracts/<tool_name>.json
   uv run python scripts/eval_contracts.py contracts/<tool_name>.json
   ```
5. **Open a PR.** `contract-gate.yml` runs three jobs:
   - **structural** — your contract against the schema, plus the cross-reference checks below,
     plus a self-test that the known-bad fixtures are still rejected (the gate guarding itself);
   - **semantic** — a quality review of routing hints, sibling confusion, careless input
     surfaces (LLM-as-judge with an API key, deterministic heuristics without one — fork PRs get
     a real signal either way);
   - **buildable** — the registry artifact builds with your contract in it (catches duplicate
     tool names before merge).
6. **Address review.** Rejections name the field and explain the fix — see the sample below.
7. **Merge = approval.** `publish-registry.yml` builds the registry, publishes an immutable
   release plus the rolling `registry-latest`, and notifies the engine. The engine then verifies
   it can *execute* your contract before pinning it — a contract can pass this repo's gate and
   still be rejected there (an unsupported feature, an unknown `api` value), so watch its
   consume-registry run if your tool never appears.

## Conventions and rules

Rules marked **enforced** fail the gate. The rest are conventions your reviewer will hold you to.

### Naming and files

- Tool names are `snake_case`, verb-first, and specific: `list_coupons`, `get_coupon`,
  `create_coupon`, `update_coupon_status`. **Enforced:** pattern `^[a-z][a-z0-9_]*$`, max 64
  chars, unique across the whole registry.
- The file is named after the tool: `contracts/list_coupons.json`.
- Tool arguments are `camelCase` (`couponId`); wire names stay whatever the upstream uses
  (`coupon_id`), connected by the parameter mapping. Don't leak wire naming into the agent's
  surface.

### Versioning

- `contractVersion` is semver for the contract itself, starting at `1.0.0`. **Enforced:**
  `x.y.z` format.
- Bump **patch** for hint/description edits, **minor** for added optional arguments, **major**
  for anything that changes the call. Any bump retires the engine's cached generated code.

### The interface (what the agent routes on)

- `description`: one sentence saying what the tool *returns*. **Enforced:** ≥ 20 chars. The
  semantic gate also rejects boilerplate and too-short-to-route descriptions.
- `whenToUse`: ≥ 1 concrete situation a user would actually raise (**enforced**), each ≥ 10 chars.
- `whenNotToUse`: name the sibling — *"The merchant named one coupon — use `get_coupon`
  instead."* This line is what stops an agent picking a list endpoint when the user named a
  record. The semantic gate rejects contracts without it.
- Destructive tools must demand explicit intent in their hints — *"the merchant explicitly asks
  to delete…"*. **Enforced** by the semantic gate for DELETEs and anything marked destructive.
- `annotations` uses **MCP's exact `ToolAnnotations` field names**, and all four are required so
  intent is stated rather than defaulted: `readOnlyHint` (modifies nothing), `destructiveHint`
  (may destroy or irreversibly change data), `idempotentHint` (same call twice, no extra
  effect), `openWorldHint` (touches a live external world). **Enforced:** `readOnlyHint: true`
  cannot pair with `destructiveHint: true`, and the method/governance rules below key off these.
- `input.schema` exposes **only the arguments an agent should set**. An upstream list endpoint
  with twenty filters becomes a tool with three. Narrowing is the contract doing its job; set
  `additionalProperties: false`.
- `response.schema` describes the **unwrapped payload** — what is inside the envelope's `data` —
  never `{status, success, data}` itself. The optional `ui` block hints rendering, with
  `{braces}` interpolating response fields.

### The binding (where it calls)

- `api` selects the configured upstream; it defaults to `"salla"`. A new value requires the
  engine to be configured for that upstream first — coordinate before inventing one.
- **Never a base URL, never a secret.** **Enforced:** the schema has no field for either.
- `summary` and `docsUrl` are optional traceability; include them whenever the upstream
  publishes them — they are what lets a reviewer check the contract against its source.
- `path` keeps the upstream's placeholders verbatim: `/coupons/{coupon_id}`.
- **Every `{placeholder}` needs a `parameters.path` entry, and every mapping must read a declared
  argument** — **enforced** by cross-reference checks, in both directions.
- **Every required argument must actually reach the request** (path, query, or body) —
  **enforced**. Requiring an argument and dropping it is a promise the tool cannot keep.
- Paginated endpoints expose a `page` argument, or the agent can only ever see page one —
  **enforced** by the semantic gate.
- Array query values default to Laravel bracket style (`status[]=a&status[]=b`) via
  `style: "bracket"`; use `constant` to pin a filter the agent must not control.
- `response.dataPath` is almost always `"data"`; `successStatuses` should match the upstream
  (Salla: 201 on some creates, 202 on deletes). **Enforced:** `pagination: "standard"` implies
  `collection: true`.

### Auth and scopes

- Scopes follow the `<domain>.read` / `<domain>.read_write` convention (**enforced** format),
  adopted from Salla and applied platform-wide.
- **Least privilege is enforced both ways:** a read-only tool may only hold `.read` scopes, and
  a writing tool must hold a `.read_write` scope. Grants are made per app installation, so an
  over-broad ask affects every store that installs the app.

### Governance (all enforced)

The gate cross-checks the annotations, the HTTP method, and the policy layer against each other:

| Rule | Why |
|---|---|
| `GET` ⇒ `readOnlyHint: true` | otherwise it is never cached and the agent is told it has side effects |
| `POST/PUT/PATCH/DELETE` ⇒ `readOnlyHint: false` | a write marked read-only would be cached — a stale result served as though the write happened |
| `DELETE` ⇒ `destructiveHint: true` + `humanApproval: "required"` | removing real data always gets a human in the loop |
| `destructiveHint` ⇒ never cacheable | caching a write is a correctness bug, not a preference |
| cacheable ⇒ `readOnlyHint: true` and `ttlSeconds` set | only reads may cache, and a cache with no TTL never expires |

- `keyBy` lists the arguments that actually affect the result — **enforced** to name real
  arguments. Typical TTLs: 60–600s for store data.
- Non-DELETE writes may run `direct`, but anything a user would want to confirm belongs in
  `propose-apply` (**enforced:** propose-apply always pairs with `humanApproval: "required"`).

### Validation guards

- `validation.rules` are regexes compiled into the generated code, so a malformed argument fails
  locally instead of costing a round trip and an upstream 422. Each rule's `field` must be a
  declared argument (**enforced**).

### Dependencies

- When your tool leans on another contract — ids that come from a list tool, state another
  binding establishes — declare it: `{"contract": "list_coupons", "reason": "coupon ids come
  from list_coupons"}`. It documents the relationship for the reviewer and the agent. The POC
  engine does not resolve or order dependencies yet, so nothing breaks without them — but a
  reviewer will ask.

## What a rejection looks like

Rejections name the field, quote the rule, and say what to do:

```
REJECTED  contracts/list_coupons.json
    - binding/http/auth/scopes/0: 'coupons.read_write' does not match '\.read$'
        -> least privilege: a read-only tool must not request a read_write scope.
           Use the .read scope, or set annotations.readOnly to false if it really writes
    - path contains {coupon_id} but parameters.path has no entry for it,
      so the request URL would keep the literal placeholder
```

## Local commands

```bash
uv sync --extra dev
uv run pytest                                 # 54 tests
uv run python scripts/validate_contracts.py   # structural + cross-reference checks
uv run python scripts/eval_contracts.py       # semantic review (heuristics without a key)
uv run python scripts/build_registry.py       # -> dist/registry.generated.json
pwsh scripts/demo_gate.ps1                    # watch bad contracts get rejected
```

## Future work

Two contract kinds existed in an earlier draft of the schema and were removed to keep v1 minimal
— one endpoint, one tool, one file, one review. They remain on the roadmap; `kind` stays in the
format so they can return without breaking existing contracts:

- **`multi-tool` packages** — several endpoints from one domain (all the Coupons endpoints,
  say) shipped and reviewed as a unit, expanded into individual tools at registry load. Worth it
  once domains grow past a handful of files with a shared owner and lifecycle.
- **`openapi-import`** — pick the operations you want from an upstream's OpenAPI spec and let
  a converter emit full single-tool contracts, which then flow through this same gate. The
  accelerator for onboarding a large surface without hand-writing every contract.

Also ahead: engine support for the `http` binding described here (envelope unwrapping, parameter
mapping, per-merchant token resolution) — tracked in `cm_mcp_engine`.

## What this repository cannot check

- **Whether the engine can execute your contract.** Only the engine knows which binding features
  and upstreams it implements; its consume-registry workflow verifies every published registry
  before pinning it.
- **Whether the upstream actually behaves as documented.** Contracts are written from docs; the
  engine's integration against a real store is where reality is tested.

## Maintainer setup

Secrets: `ANTHROPIC_API_KEY` (optional — upgrades the semantic gate from heuristics to
LLM-as-judge), `ENGINE_DISPATCH_TOKEN` (fine-grained PAT, *Contents: read and write* on
`cm_mcp_engine`; without it the engine falls back to its daily scheduled pull). Replace
`@owner`/`@reviewers` in [CODEOWNERS](CODEOWNERS) with real handles, and protect `main` requiring
the `structural`, `semantic`, and `buildable` checks plus a CODEOWNERS review on `contracts/**`.

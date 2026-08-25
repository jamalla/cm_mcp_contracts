# Contract-authoring skill — the briefing, made persistent

[contributor-prompt.md](contributor-prompt.md) is a briefing you hand over once, per endpoint.
This is the same job as a **skill**: a file an agent loads by itself whenever the task looks like
"add a contract for `<endpoint>`", so the conventions, the gate's rules and the traps do not have
to be restated — or remembered — each time.

Save it as `.claude/skills/salla-tool-contract/SKILL.md`, either inside your clone of this
repository or at the root of a workspace holding it. The frontmatter's `description` is what an
agent matches against, so keep it if you adapt the rest.

It carries what a first-time contributor learns the hard way: the two documentation pointers that
have gone stale, the A2UI binding rule that fails silently, and the checks the semantic gate runs
that the schema does not.

---

````markdown
---
name: salla-tool-contract
description: Submit a new tool contract to cm_mcp_contracts - wrap one upstream REST endpoint (usually a Salla Admin API endpoint) as a JSON contract, validate it against the gate, and open the PR. Use whenever the task is "add a contract for <endpoint>", "add a tool that lists/gets/creates X in Salla", or when handed a contributor briefing like docs/contributor-prompt.md
---

# Submitting a tool contract to cm_mcp_contracts

One endpoint, one tool, one file. The entire diff is **one new file**:
`contracts/<tool_name>.json`. Never touch the schema, scripts, templates, workflows, or
another contract.

## 0. Read the briefing carefully first

A briefing is often `docs/contributor-prompt.md` with the endpoint swapped in, and the swap
is frequently incomplete — the prose may still say "List Categories" while the GOAL names
`GET /branches`. **The path, the tool name and the docs link are the binding facts; a
leftover title in the prose is not.** Say so in the final report rather than silently
picking one.

## 1. Ground yourself in the repo (in this order)

1. `README.md` end to end — the conventions and the enforced rules.
2. The rulebook the gate actually loads: `SCHEMA_PATH` in `scripts/validate_contracts.py`,
   which is also what `templates/single-tool.template.json` puts in `$schema`. Older
   versions are retired rather than kept alongside, so there is exactly one.
3. `templates/single-tool.template.json` — the starting point.
4. The nearest sibling already in `contracts/` (a list endpoint for a list, a detail for a
   detail). Match its house style — it has already passed the gate.
5. Skim `scripts/validate_contracts.py` (cross-reference checks) and
   `scripts/eval_contracts.py` (the semantic judge's failure list). Both tell you exactly
   what will reject you.

## 2. Research the endpoint — never invent a binding fact

Method, path, scope, query parameters, response envelope, pagination, success status and the
documented failure statuses are **copied** from the upstream docs. Two sources, use both:

- The endpoint's docs page (e.g. `https://docs.salla.dev/branches/list`) — keep this URL, it
  becomes `docsUrl`.
- Salla's published OpenAPI spec, which gives the exact parameters, scope string, response
  example and documented error statuses per verb. (The `salla-platform-docs` MCP server
  serves it: list every path, then read the `$ref` for the one you want.)

Where the rendered page and the spec disagree on the filter list, prefer the spec for what
the endpoint *accepts*, and expose fewer than either.

## 3. Fill the contract

Copy the template to `contracts/<tool_name>.json`; the file is named after `interface.name`.

**Interface**
- `name`: `snake_case`, verb-first (`list_branches`). `title`: human readable.
- `description`: one sentence saying what it *returns*, no template boilerplate.
- `whenToUse`: concrete merchant situations, quoting the kind of thing a merchant says.
- `whenNotToUse`: **name the sibling tool** — the list/detail confusion (`get_branch`), the
  read/write confusion (`create_branch`, `update_branch`), the adjacent-domain confusion. The
  sibling may not exist yet; name it anyway. Omitting this fails the semantic gate.
- `annotations`: MCP's exact four names. A GET is `readOnlyHint: true`,
  `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: true`. A write is
  `readOnlyHint: false`; a DELETE is additionally `destructiveHint: true` **and** needs
  `humanApproval: "required"`, plus a `whenToUse` hint demanding explicit intent ("the
  merchant explicitly asks to delete…").
- `input.schema`: **camelCase** arguments, `additionalProperties: false`, and deliberately
  fewer filters than the upstream accepts. Expose what an agent can set correctly from a
  merchant's own words; drop internal codes, response-expansion switches (`with`), page-size
  knobs, and anything that leaks unrelated data. Be ready to justify every exclusion in the
  PR body. **A paginated endpoint must expose `page`** — enforced by the semantic gate.
- `response.schema`: **ONE record**, unwrapped, ~5–6 fields an agent genuinely routes on
  (always the `id`). For a collection the engine returns `{items, count, pagination}` and
  shapes each item to this schema — still describe the single record. Never model
  `{status, success, data}`.

**The A2UI surface** (`response.ui`) — a table fits a listing:
- Flat component list, exactly one `root`, parents name children by id, nothing unreachable.
- Presentation components only: `Text`, `Column`, `Row`, `List`, `Card`, `Divider`, `Image`.
- **Leading `/` reads from the result; no leading slash reads from the current item inside a
  template.** Getting this backwards renders every row blank and no test catches it.
- Every bound path — including pointers inside a `formatString` template — must name a field
  in `response.schema` (or `count`/`total`/`perPage`/`currentPage`/`totalPages` on a
  collection).
- Salla money is `{amount, currency}` — use `formatCurrency`, never a bare path.
- Working table shape: `root` Column → `heading` (`pluralize` over `/count`), a literal
  header Row, a `Divider`, then a `List` whose `children` is
  `{componentId: "row", path: "/items"}`; give the header cells and row cells matching
  `weight`s so the columns line up.

**Binding**
- No base URL, no token, no secret — there is no field for them.
- `path` keeps the upstream's `{placeholders}` verbatim, and each needs a `parameters.path`
  entry.
- `parameters.query`: `name` is the wire name, `from` is the camelCase argument
  (`{"name": "is_default", "from": "isDefault"}`). Omit `from` when they match. Every mapping
  must read a declared argument, and every required argument must reach the request.
- `auth.scopes`: the narrowest scope the docs state. A read-only tool may hold only `.read`;
  a writing tool must hold a `.read_write`.
- `response`: `dataPath: "data"`, `collection`/`pagination` truthfully
  (`pagination: "standard"` implies `collection: true`), `successStatuses` from the docs.
- `response.errors`: only the statuses the endpoint documents (for most Salla reads, just
  `401`), each with a `meaning` written for the agent to relay to a merchant — "the token
  does not carry branches.read, so the branches cannot be read until the app's authorization
  is fixed", not "unauthorized". Add `fieldsPath` only where a 422 exists.

**Governance and the rest**
- `caching`: cacheable only for reads, `keyBy` naming real camelCase arguments, `ttlSeconds`
  60–600 for store data (longer only for near-static config — justify it in a `_comment`).
- `validation.rules` are optional; add one only where a regex genuinely guards an argument
  that `input.schema` cannot (an enum or `minimum` already does the job). Each rule's `field`
  must be a declared argument.
- `dependencies`: only contracts that **already exist in `contracts/`** — the validator
  rejects an edge pointing at a tool the lane does not carry. A sibling named in prose is
  fine and unchecked.
- `contractVersion`: `1.0.0` for a new contract.

## 4. Validate locally — all three gate jobs

```bash
uv sync --extra dev
uv run python scripts/validate_contracts.py contracts/<tool_name>.json   # structural
uv run python scripts/eval_contracts.py contracts/<tool_name>.json       # semantic
uv run python scripts/build_registry.py                                  # buildable
```

Also run `validate_contracts.py` with no arguments once, so the cross-contract checks see the
whole lane. Fix every rejection — the messages name the field and the fix.

## 5. Branch, commit, PR

- Branch from `main`: `tool/<tool_name>`.
- Commit **only** the contract file. Message: `Add <tool_name> contract`, plain, committed as
  yourself. **No AI attribution trailer, ever.**
- Push, then open the PR titled `Add <tool_name> contract` against `main`.
- Where the `gh` CLI is unavailable, the git credential helper plus the REST API works:

```bash
TOKEN=$(printf 'protocol=https\nhost=github.com\n\n' | git credential fill 2>/dev/null \
        | grep '^password=' | cut -d= -f2-)
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" \
     https://api.github.com/repos/jamalla/cm_mcp_contracts/pulls \
     --data-binary @pr.json | python -c "import json,sys;print(json.load(sys.stdin)['html_url'])"
```

Some shells here reject heredocs (`<<'EOF'`): write the contract JSON and the PR body to
files with an editor tool, and build `pr.json` with a small `python -c` that reads the body
file, rather than inlining multi-line content in a shell command.

The PR body must carry: what the tool does, the docs URL worked from, and **which upstream
filters you chose not to expose and why** (a short table reads well).

## 6. Watch the gate, then stop

Poll the checks and do not merge:

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://api.github.com/repos/jamalla/cm_mcp_contracts/commits/tool%2F<tool_name>/check-runs"
```

Three jobs must go green: **Structural validation**, **Semantic review (LLM-as-judge)**,
**Registry builds**. If one fails, read its output, fix the contract, push again. Then hand
back the PR URL, the check results, and a note about anything ambiguous in the briefing — and
leave it for review.
````

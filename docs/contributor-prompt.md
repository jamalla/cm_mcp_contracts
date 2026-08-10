# Contributor prompt — worked example

A ready-to-use briefing for delegating a contract submission to a contributor, human or AI
agent. Hand it over verbatim; it points them at the README and the schema as the source of
truth, makes them research the endpoint themselves on the upstream's docs (that research is
part of the job — binding facts are copied, never invented), and walks them through the
branch → fill → validate → PR flow without letting them merge.

The example below submits **`list_categories`** (Salla Admin API, *List Categories*,
`GET /categories`). To reuse it for another endpoint, swap three things:

1. the endpoint name and path in **GOAL** and the docs pointer in step 3;
2. the tool name everywhere it appears — branch, file, PR title (`list_categories` →
   `your_tool_name`);
3. the endpoint-specific guidance in step 5 — the pagination note applies to list
   endpoints; a detail endpoint would instead stress the path-parameter mapping, and a
   write endpoint the annotations (`readOnlyHint: false`, DELETE ⇒ `destructiveHint: true`
   with human approval) and the `.read_write` scope.

---

````
You are contributing a new tool to the contract registry at
https://github.com/jamalla/cm_mcp_contracts

GOAL
Submit a pull request adding ONE tool contract that lets an agent list a Salla
store's categories, wrapping the Salla Admin API endpoint "List Categories"
(GET /categories). You write a JSON contract — no server code.

BEFORE WRITING ANYTHING
1. Read the repository README end to end. It defines the contract anatomy, the
   naming conventions, and the rules the gate enforces. Treat it as binding.
2. Read schema/tool-contract.v1.json (the rulebook) and
   templates/single-tool.template.json (your starting point).
3. Research the endpoint yourself on https://docs.salla.dev/ (Merchant API →
   Categories → List Categories). The binding facts — method, path, required
   scope, query parameters, response envelope and pagination, success status —
   are copied from there, never invented. Keep the docs URL; it goes in the
   contract as docsUrl.

THE WORK
4. Branch from main: tool/list_categories
5. Copy templates/single-tool.template.json to contracts/list_categories.json
   and fill it in. Honor these points, all from the README:
   - tool name: list_categories; file named after the tool.
   - interface.annotations uses MCP's exact names (readOnlyHint,
     destructiveHint, idempotentHint, openWorldHint) — set them truthfully for
     a read-only listing endpoint.
   - whenToUse: concrete merchant situations. whenNotToUse: name the sibling
     tool a merchant asking about ONE specific category would need (it may not
     exist yet — name it anyway).
   - input schema: camelCase arguments, and expose ONLY the filters an agent
     genuinely needs — fewer than Salla accepts. The response is paginated, so
     a page argument is mandatory. Set additionalProperties: false.
   - parameters: map every exposed argument to Salla's wire names; every
     mapping must read a declared argument.
   - response: describe the UNWRAPPED payload (what is inside data), pick the
     ~5-6 fields per category an agent actually cares about, set
     collection/pagination truthfully, and add a ui hint (a table fits a
     category listing).
   - response.errors: copy the endpoint's documented failure statuses from its
     docs page and write each meaning for the agent to relay to a user.
   - auth.scopes: the narrowest scope Salla's docs state for this endpoint.
   - governance: caching only if justified, with keyBy naming real arguments
     and a sensible ttlSeconds.
   - NEVER include a base URL, a token, or any secret. They do not belong in
     contracts.
6. Validate locally before pushing:
     uv sync --extra dev
     uv run python scripts/validate_contracts.py contracts/list_categories.json
     uv run python scripts/eval_contracts.py contracts/list_categories.json
   Fix every rejection — the messages tell you what to change.

THE PR
7. Commit ONLY the new contract file on your branch. Plain commit message, no
   AI attribution trailers. Push and open a PR to main titled
   "Add list_categories contract". In the PR body: what the tool does, the
   docs.salla.dev link you worked from, and which Salla filters you chose NOT
   to expose and why.
8. Do not merge. CI (contract-gate) runs structural, semantic, and
   registry-build checks on your PR; if a job fails, read its output, fix the
   contract, and push again. Then wait for review.

DO NOT touch the schema, scripts, templates, workflows, or any other file.
Your entire diff is one new file: contracts/list_categories.json.
````

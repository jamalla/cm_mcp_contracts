# code_mode and the runtime

How `cm_mcp_engine` turns a JSON contract into Python, runs it, and reports every
stage of doing so. This is the part of the platform where "no server code" stops
being a slogan.

- [1. What code_mode is](#1-what-code_mode-is)
- [2. Generation — contract to source](#2-generation--contract-to-source)
- [3. The generated module](#3-the-generated-module)
- [4. The value resolver](#4-the-value-resolver)
- [5. Execution — the orchestrator](#5-execution--the-orchestrator)
- [6. The sandbox](#6-the-sandbox)
- [7. The two caches](#7-the-two-caches)
- [8. Propose-apply](#8-propose-apply)
- [9. Stage events](#9-stage-events)
- [10. A run, end to end](#10-a-run-end-to-end)
- [11. Failure taxonomy](#11-failure-taxonomy)

---

## 1. What code_mode is

**Deterministic template fill. No LLM.**

The contract already declares the endpoint, the parameter mapping, the envelope and
the failure modes — so generation is a template fill rather than an inference. That
makes it fast, repeatable, and **byte-stable**, which is in turn what makes the code
cache trustworthy: the same inputs always produce the same bytes, so a cache hit is
provably the same module a regeneration would have produced.

```mermaid
flowchart LR
    subgraph IN["declared in the contract"]
        I1["method · path · api"]
        I2["parameter mapping<br/>+ array styles"]
        I3["dataPath · collection<br/>successStatuses"]
        I4["documented errors<br/>+ their meanings"]
        I5["validation.rules<br/><i>regexes</i>"]
        I6["response.schema<br/><i>the promised fields</i>"]
    end
    subgraph ENGINE["resolved by the engine"]
        E1["base_url<br/><i>from UPSTREAMS</i>"]
        E2["token_env NAME<br/><i>never a value</i>"]
        E3["auth_scheme"]
    end
    T["Jinja2 template<br/>StrictUndefined"]
    OUT["a self-contained<br/>Python module<br/><i>stdin → stdout</i>"]

    IN --> T
    ENGINE --> T
    T --> OUT

    style IN fill:#e8f0fe,stroke:#4285f4
    style ENGINE fill:#e6f4ea,stroke:#34a853
    style OUT fill:#fef7e0,stroke:#fbbc04,stroke-width:2px
```

**Nothing is invented at generation time.** Every constant in the output traces to
either the contract or the engine's upstream table.

### Why this and not an LLM

| Property | Why it matters here |
|---|---|
| repeatable | the code cache can be keyed on inputs, because outputs are a function of them |
| byte-stable | a cache hit is indistinguishable from a regeneration |
| fast | generation is sub-millisecond; the demo's first call is ~1s end to end, almost all of it network |
| auditable | the code shown in the UI's right pane is the code that ran, and a reviewer can predict it from the contract |
| no key required | the whole platform runs with no API key anywhere |

---

## 2. Generation — contract to source

### 2.1 The pipeline

```mermaid
flowchart TB
    TE["ToolEntry<br/><i>from the registry catalog</i>"] --> K{"binding.type"}
    K -->|"http"| HC["_http_context(entry)"]
    K -->|"none"| BC["_common(entry) + handler"]
    K -->|other| ERR["❌ ValueError:<br/>unsupported binding type"]

    HC --> T1["http_tool.py.j2"]
    BC --> T2["builtin_tool.py.j2"]

    T1 & T2 --> R["Environment.render(**context)<br/>StrictUndefined · keep_trailing_newline<br/>filter: pyval = repr"]
    R --> SRC["source: str"]

    HC & BC --> GID["generation_id()<br/>sha256(json({template, context}, sort_keys))[:12]"]

    style ERR fill:#fce8e6,stroke:#ea4335
    style SRC fill:#e6f4ea,stroke:#34a853,stroke-width:2px
    style GID fill:#fef7e0,stroke:#fbbc04
```

### 2.2 `_http_context` — what the contract becomes

| Context key | Source | Notes |
|---|---|---|
| `upstream`, `base_url`, `token_env`, `auth_scheme` | **the engine's `UPSTREAMS` table** | never the contract |
| `method`, `path_template`, `timeout_ms` | `binding.http` | `timeoutMs` defaults to 5000 |
| `scopes` | `binding.http.auth.scopes` | embedded so a 401 can name them |
| `path_params` | `parameters.path[]` → `{wire, arg}` | `from` defaults to `name` |
| `query_params` | `parameters.query[]` → `{wire, arg, constant, has_constant, style, resolver}` | a `constant` reads no argument — how a contract pins a filter the agent must not control |
| `body_mode`, `body_fields`, `consumed_args` | `parameters.body` | `consumed_args` is what decides where an argument lands under `passthrough` |
| `data_path`, `collection`, `pagination`, `success_statuses` | `binding.http.response` | `dataPath` split on `.` |
| `error_message_path`, `error_fields_path` | `response.errors` | default `error.message` / `error.fields` |
| `error_meanings` | `response.errors.statuses[]` | keyed **by status string**, so the generated code can name what a failure means |
| `response_fields` | `interface.response.schema.properties` | sorted — the projection allowlist |
| `required`, `forbidden`, `rules` | `interface.input` + `validation` | compiled into real guards |

### 2.3 `pyval = repr`, not `tojson`

```python
_env.filters["pyval"] = repr
```

The templates generate **Python**, so they need Python's spelling. JSON writes
`null` / `true` / `false`, which read as undefined names in Python and fail at import.
`repr` is deterministic for the `str/int/bool/None/list/dict` shapes a contract can
hold — which is precisely what keeps generation byte-stable and the code cache
trustworthy.

`StrictUndefined` means a context key the template expects and does not get is an
error at generation time, not a silently empty constant in a running tool.

### 2.4 `generation_id` — the cache identity

```python
generation_id(entry) = sha256(json({"template": …, "context": …}, sort_keys=True))[:12]
```

**Deliberately wider than the contract.** Three cases it covers that a version number
does not:

```mermaid
flowchart TB
    subgraph WHY["what changes the generated bytes"]
        W1["the contract's binding<br/><i>the obvious case</i>"]
        W2["the engine's UPSTREAMS table<br/><i>a host moved</i>"]
        W3["DEV_OFFLINE flipping<br/><i>mock ⇄ production host</i>"]
        W4["a contract corrected without a<br/>contractVersion bump<br/><i>nothing forces the bump</i>"]
    end
    subgraph NOT["what does NOT change it — correctly"]
        N1["a reworded description"]
        N2["a new whenToUse hint"]
        N3["<i>they change what the AGENT is told,<br/>not what the code does</i>"]
    end
    WHY --> GID(["generation_id"])
    NOT -.->|"no effect"| GID

    style WHY fill:#e8f0fe,stroke:#4285f4
    style NOT fill:#f1f3f4,stroke:#9aa0a6
```

Without W3, turning `DEV_OFFLINE` off would hand back the offline module from the
on-disk cache and **quietly keep calling a mock instead of the store** — the on-disk
code cache outlives a restart, so this is not hypothetical.

### 2.5 `required_secrets`

```python
required_secrets(entry) -> [upstream.token_env]   # http bindings only; [] for builtins
```

The name comes from the **engine's upstream table, never from the contract** — a
contract cannot request a token. This list is exactly what the sandbox will inject.

---

## 3. The generated module

### 3.1 Two templates

| Template | For | Shape |
|---|---|---|
| `http_tool.py.j2` | `binding.type: "http"` | self-contained; imports only `json`, `os`, `re`, `sys`, `urllib.parse.quote`, `httpx` |
| `builtin_tool.py.j2` | `binding.type: "none"` | imports `cm_engine.engine.builtins.BUILTINS` and dispatches on the `builtin://name` handler; **no network, no secrets, works with the machine unplugged** |

Both share the same harness: read an args JSON object on stdin, write a result JSON
object on stdout. That is what lets the sandbox run them as a plain subprocess.

### 3.2 Anatomy of an HTTP tool module

```mermaid
flowchart TB
    subgraph CONST["module constants — all from the contract or the upstream table"]
        C1["CONTRACT = 'list_orders@3.0.0'"]
        C2["UPSTREAM · BASE_URL · METHOD · PATH_TEMPLATE · TIMEOUT_SECONDS"]
        C3["TOKEN_ENV = 'SALLA_ACCESS_TOKEN'  ← the NAME"]
        C4["SCOPES · PATH_PARAMS · QUERY_PARAMS · BODY_MODE · BODY_FIELDS"]
        C5["REQUIRED · FORBIDDEN · CONSUMED_ARGS"]
        C6["DATA_PATH · COLLECTION · PAGINATION · SUCCESS_STATUSES"]
        C7["RESPONSE_FIELDS · PAGINATION_FIELDS"]
        C8["ERROR_MESSAGE_PATH · ERROR_FIELDS_PATH · ERROR_MEANINGS"]
    end

    subgraph FN["functions"]
        F1["validate(args)<br/><i>required · forbidden · the contract's regexes<br/>as REAL compiled guards</i>"]
        F2["resolve_values(args)<br/><i>readable value → this store's id</i>"]
        F3["build_path · build_query · build_body"]
        F4["describe_failure(response)<br/><i>status → the sentence the contract wrote</i>"]
        F5["shape(payload) → project(value)<br/><i>unwrap the envelope, keep promised fields</i>"]
        F6["run(args) — the orchestration"]
    end

    MAIN["__main__:<br/>read stdin → run() →<br/>print {ok, result} or {ok:false, error}"]

    CONST --> FN --> MAIN

    style C3 fill:#fce8e6,stroke:#ea4335,stroke-width:2px
    style MAIN fill:#e6f4ea,stroke:#34a853
```

### 3.3 `run()` — the request lifecycle

```mermaid
flowchart TB
    A(["args from stdin"]) --> V["<b>validate</b><br/>required present · forbidden absent<br/>every validation.rule regex"]
    V -->|fail| TE["ToolError"]
    V --> RV["<b>resolve_values</b><br/>readable names → store-specific ids<br/><i>one extra GET, before the real call</i>"]
    RV -->|"no match, onMiss: error"| TE
    RV --> BP["<b>build_path</b><br/>{placeholder} → quote(value, safe='')<br/><i>an id with a slash cannot reshape the URL</i>"]
    BP --> BQ["<b>build_query</b><br/>(name, value) PAIRS, not a dict<br/>styles: single · bracket · repeat · csv<br/>+ pinned constants"]
    BQ --> BB["<b>build_body</b><br/>none · mapped · passthrough"]
    BB --> H["headers: accept +<br/>authorization = Bearer os.environ.get(TOKEN_ENV)"]
    H --> REQ["httpx.request(METHOD, url, params, json, headers, timeout)"]
    REQ -->|"TimeoutException"| TE
    REQ -->|"HTTPError"| TE
    REQ --> ST{"status in SUCCESS_STATUSES?"}
    ST -->|no| DF["<b>describe_failure</b>"] --> TE
    ST -->|yes| JS{"body parses as JSON?"}
    JS -->|no| TE
    JS --> SH["<b>shape</b> — walk DATA_PATH<br/>collection? → {items, count, pagination}<br/>each item → <b>project</b> to RESPONSE_FIELDS"]
    SH -->|"dataPath missing"| TE
    SH --> OK(["{ok: true, result: …} → stdout"])
    TE --> BAD(["{ok: false, error: …} → stdout"])

    style OK fill:#e6f4ea,stroke:#34a853,stroke-width:2px
    style BAD fill:#fce8e6,stroke:#ea4335
```

### 3.4 Four details worth calling out

**Query parameters are pairs, not a dict.** Bracket and repeat styles send the same
name more than once — which is how Laravel-shaped upstreams take arrays
(`status[]=1&status[]=2`). A dict cannot express that.

**Projection is a leak guard, not a formatting step.** `project()` keeps only the
fields `interface.response.schema` promised, so **a chatty upstream adding a field
does not leak it to the agent**, and the agent sees the shape it was told to expect.
Pagination is projected for the same reason and against a fixed allowlist
(`count, total, perPage, currentPage, totalPages`) — Salla sends prebuilt `links`
carrying an internal app id and an admin URL, and those stop here rather than
travelling into a model's context.

**A list endpoint and a single-record endpoint describe the same record.**
`response.schema` always describes **one** item, unwrapped. For a collection the
engine returns `{items, count, pagination}` and shapes each item to that schema — so a
category is described identically whether the tool fetches one or many.

**Failure becomes a sentence, not a body dump.** `describe_failure` assembles:

```
list_orders@3.0.0: salla returned 401
  -- The store's access token is missing, expired, or does not carry the orders.read scope…
  -- upstream said: "Unauthenticated."
  -- this endpoint needs the orders.read scope
  -- retrying the call unchanged may succeed        [only if retryable]
```

An **undocumented** status says so explicitly: *"a status this contract does not
document for this endpoint"*. `retryable` follows the contract's declaration where it
made one, and otherwise defaults to `429 or >= 500`.

---

## 4. The value resolver

The v2 schema addition, and the most subtle piece of the runtime.

**The problem.** The tool takes `status: ["shipped"]` — a word a person would say.
Salla's order filter wants the numeric id that state has **in this store**, which
differs per merchant and so cannot be written into a contract or known by an agent.
And Salla does not *reject* a filter it cannot use — **it ignores it and returns
everything.** The wrong answer arrives looking exactly like the right one.

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent
    participant M as generated module
    participant L as GET /orders/statuses
    participant O as GET /orders

    A->>M: status = ["shipped"]
    Note over M: resolve_values(), before anything is built
    M->>L: one GET on the caller's credential
    L-->>M: data[] = [{id: 1201821018, slug: "shipped", name: "تم الشحن"}, …]
    Note over M: match_record — EQUALITY, case-folded,<br/>across matchOn = [slug, name, translations.en.name]
    M->>M: substitute → status = [1201821018]
    M->>O: GET /orders?status[]=1201821018
    O-->>M: the orders that were actually asked for
```

**Declared in the contract, inlined into the module:**

```jsonc
{ "name": "status", "style": "bracket",
  "resolve": {
    "contract":  "list_order_statuses",   // the sibling this copy must agree with
    "path":      "/orders/statuses",
    "dataPath":  "data",
    "matchOn":   ["slug", "name", "translations.en.name"],
    "sendField": "id",
    "onMiss":    "error"
  } }
```

Everything is inlined at generation time because **the module runs in a sandbox with
no registry to consult.** That duplication is only safe while something compares the
copy to the original — which is `resolver_problems()` in the contracts gate. It checks
four things:

| Check | Guards against |
|---|---|
| the resolver's target is in this contract's `dependencies` | an undeclared edge |
| `path` and `dataPath` match the named contract's own | the inlined copy drifting from its source |
| the target is **read-only** | a resolver runs *before* the call anyone asked for, so it must never change anything |
| the target's scopes are a subset of this tool's | a lookup widening the blast radius |

**`match_record` uses equality, not containment**, case-folded and whitespace-stripped:
a store with both `delivered` and `delivering` must not have one request quietly widen
into the other.

**`onMiss: "error"` is the point of the whole feature.** A value that matches nothing
fails the call and names what the store *does* have:

```
list_orders@3.0.0: status -- this store has no 'refunded'.
It has: canceled, completed, delivered, delivering, in_progress, payment_pending, restored, shipped, under_review
```

Compare the alternative: dropping the filter and returning every order in the store,
presented to the merchant as "your refunded orders".

---

## 5. Execution — the orchestrator

[`executor.py`](../../cm_mcp_engine/cm_engine/engine/executor.py) — cache → codegen →
sandbox → cache, emitting as it goes.

```mermaid
flowchart TB
    START(["Executor.run(tool_name, args, run_id, sink,<br/>approval_token=None, principal=None)"])
    START --> P0["principal = principal or default_principal()<br/><i>a KEYWORD argument, never a member of args</i>"]
    P0 --> LOOK{"registry.catalog.get(tool_name)"}
    LOOK -->|KeyError| E0["emit error(stage=contract_selected)<br/>→ ExecutionOutcome('error')"]
    LOOK --> CS["📤 <b>contract_selected</b><br/>{contractName, version, contract, uiHint}"]
    CS --> GEN["generation = codemode.generation_id(entry)<br/>key = cache_key(entry, args, generation, principal.cache_scope)"]

    GEN --> C1{"is_cacheable(entry)<br/>and result_cache.get(key) hits?"}
    C1 -->|HIT| CH["📤 <b>cache_hit</b> {key, storedAt, expiresAt}<br/>📤 <b>result</b> {output, fromCache: true, uiHint}<br/>📤 <b>done</b> {durationMs, cached: true}"]
    CH --> RET1(["ok, cached — single-digit ms"])

    C1 -->|miss| A1{"entry.needs_approval?"}
    A1 -->|"yes, no token"| PROP["📤 <b>proposal</b> {contractName, action,<br/>args, approvalToken, reason}<br/>📤 <b>done</b> {durationMs, proposed: true}"]
    PROP --> RET2(["proposed — nothing mutated"])
    A1 -->|"yes, bad token"| BADT["📤 <b>error</b> — hmac.compare_digest failed"]
    A1 -->|"no, or token ok"| CG

    CG["code_key = f'{entry.key}+{generation}'<br/>source = code_cache.get(code_key)<br/>or codemode.generate(entry)<br/>module_path = code_cache.put(...)"]
    CG -->|exception| E1["📤 <b>error</b> (stage=code_generated)"]
    CG --> CGE["📤 <b>code_generated</b><br/>{code, fromCache, language: python, cacheKey}"]
    CGE --> EX["📤 <b>executing</b> {tool, binding, principal}"]
    EX --> INJ["injected = {TOKEN_ENV: provider.resolve(upstream, principal)}<br/><i>resolved PER CALL, not per process</i>"]
    INJ --> SB["await run_module(module_path, args, secrets=injected)"]
    SB -->|"not ok"| E2["📤 <b>error</b> (stage=executing)<br/>📤 <b>done</b> {failed: true}"]
    SB --> RES["📤 <b>result</b> {output, fromCache: false, uiHint}"]
    RES --> C2{"is_cacheable(entry)?"}
    C2 -->|yes| CST["result_cache.put(key, output, ttl)<br/>📤 <b>cache_store</b> {key, ttlSeconds}"]
    C2 -->|no| DONE
    CST --> DONE["📤 <b>done</b> {durationMs, cached: false}"]
    DONE --> RET3(["ok"])

    style CH fill:#fef7e0,stroke:#fbbc04,stroke-width:2px
    style PROP fill:#fce8e6,stroke:#ea4335
    style RET1 fill:#e6f4ea,stroke:#34a853
    style RET3 fill:#e6f4ea,stroke:#34a853
```

### 5.1 The cache-hit short circuit is the demo's climax

On a result-cache hit the executor emits `cache_hit` and returns immediately. It does
**not** emit `code_generated` or `executing` — *because those stages genuinely did not
happen.* The right pane is visibly shorter and faster on the second prompt, and it is
shorter for a true reason rather than a presentational one.

```mermaid
flowchart LR
    subgraph FIRST["first call — ~1s"]
        direction TB
        f1[prompt_received] --> f2[routing] --> f3[contract_selected] --> f4[code_generated] --> f5[executing] --> f6[result] --> f7[cache_store] --> f8[done]
    end
    subgraph SECOND["same prompt again — single-digit ms"]
        direction TB
        s1[prompt_received] --> s2[routing] --> s3[contract_selected] --> s4[cache_hit] --> s6[result] --> s8[done]
    end

    style FIRST fill:#e8f0fe,stroke:#4285f4
    style SECOND fill:#e6f4ea,stroke:#34a853,stroke-width:2px
```

### 5.2 One cached module serves every principal

The generated module carries no credential — only the **name** of the environment
variable the sandbox will fill. So the code cache omits the principal on purpose: the
module is identical for everyone *precisely because the credential is not in it*. The
token is injected per call.

---

## 6. The sandbox

[`sandbox.py`](../../cm_mcp_engine/cm_engine/engine/sandbox.py). **Not a security
boundary** — a subprocess shares the filesystem and the network with its parent. This
stops accidents and runaway loops, not a determined attacker. Running untrusted
partner code would need a container or a jailed interpreter, explicitly out of scope
for the POC.

**What it does buy:** a real process boundary, a hard timeout, and an environment
carrying only the secrets the contract implied.

```mermaid
flowchart LR
    subgraph PARENT["engine process"]
        EXE["Executor"]
    end
    subgraph CHILD["subprocess — sys.executable module_path"]
        MOD["the generated module"]
    end

    EXE -->|"stdin: json.dumps(args)"| MOD
    MOD -->|"stdout: an ok envelope, or an error one"| EXE
    MOD -->|"stderr: last 500 chars on non-zero exit"| EXE

    ENV["<b>env = allowlist + this tool's one credential</b><br/>PATH · SYSTEMROOT · COMSPEC · TEMP · TMP · LANG · LC_ALL<br/>+ PYTHONPATH=REPO_ROOT · PYTHONIOENCODING=utf-8<br/>+ SALLA_ACCESS_TOKEN<br/><i>everything else in the parent env is dropped</i>"]
    ENV --> CHILD

    TO["⏱ asyncio.wait_for — 15s hard timeout<br/>→ process.kill()"]
    TO -.-> CHILD

    style ENV fill:#e6f4ea,stroke:#34a853,stroke-width:2px
    style CHILD fill:#e8f0fe,stroke:#4285f4
```

**Five ways a run comes back not-ok**, each with a distinct message:

| Condition | `SandboxResult.error` |
|---|---|
| `OSError` starting the process | `could not start sandbox: …` |
| exceeded 15s | `tool exceeded its 15.0s sandbox timeout` |
| non-zero exit | `sandbox exited {code}: {last 500 chars of stderr}` |
| empty stdout | `tool produced no output` |
| stdout is not JSON | `tool wrote non-JSON to stdout: {first 300 chars}` |
| `{"ok": false}` envelope | the module's own `ToolError` text — the contract's sentence |

`PYTHONPATH=REPO_ROOT` is there because the **builtin** template imports
`cm_engine.engine.builtins`; the HTTP template does not.

---

## 7. The two caches

```mermaid
flowchart TB
    subgraph CODE["code cache — on disk, .cache/code/*.py"]
        CK["key = <b>{name}@{version}+{generation}</b><br/>flattened: @ and + → __"]
        CN["<i>no principal</i> — the module is identical<br/>for everyone because the credential is not in it"]
        CL["survives restart · memory layer in front"]
    end
    subgraph RESULT["result cache — in memory, TTL"]
        RK["key = <b>name@version</b> : first 16 hex of sha256 over<br/>entry.key · generation · principal · canonical(keyBy args)"]
        RG["written ONLY if <b>is_cacheable(entry)</b>:<br/>read_only AND caching.cacheable"]
        RL["lost on restart · no eviction beyond TTL"]
    end

    style CODE fill:#e8f0fe,stroke:#4285f4
    style RESULT fill:#fef7e0,stroke:#fbbc04
```

### 7.1 The result key — four components, each a bug if left out

| Component | Left out ⇒ |
|---|---|
| **the tool** (`entry.key`) | two tools collide |
| **the generation** | a corrected contract answered from its predecessor's results; `DEV_OFFLINE` off still serving mock-shaped answers |
| **the principal** | two merchants asking the same question see each other's data — *a breach rather than a bug, so it is a required argument with no default* |
| **the `keyBy` arguments** | either a trace id fragments the cache, or an argument that matters is ignored |

`keyBy` lets a contract declare which arguments actually affect the result, and the
gate enforces that they name real arguments.

### 7.2 `is_cacheable` — one gate, one place

```python
def is_cacheable(entry) -> bool:
    return bool(entry.read_only) and bool(entry.caching.get("cacheable"))
```

Both conditions must hold. And `read_only` is **derived, not believed**:

```python
@property
def read_only(self) -> bool:
    if self.method in {"POST", "PUT", "PATCH", "DELETE"}:
        return False                      # the verb decides
    return bool(self.annotations.get("readOnlyHint"))
```

The contracts gate already rejects a POST claiming `readOnlyHint: true` — but the gate
is a different machine from the one acting on the claim, and this engine also loads
sources that never passed it (a developer's working tree, a hand-edited pinned
artifact). **Caching a write is a correctness bug whatever a contract says about
itself.** The same reasoning makes `destructive` true for any DELETE, and
`needs_approval` true for any DELETE regardless of its governance block.

---

## 8. Propose-apply

A write that a human confirms before it happens.

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant UI as React UI
    participant B as BFF
    participant E as Engine (executor)

    U->>UI: "cancel order ORD-777888"
    UI->>B: POST /api/chat
    B->>E: call_tool("cancel_order", {orderId: …}, run_id)
    Note over E: entry.needs_approval && no approval_token
    E->>E: expected = HMAC(_APPROVAL_SECRET, "{entry.key}|{cache_key}")[:32]
    E-->>B: 📤 proposal {action: "POST https://api.salla.dev/admin/v2/orders/…/cancel",<br/>args, approvalToken, reason}
    E-->>B: 📤 done {proposed: true}
    B-->>UI: SSE → Approve / Reject buttons
    Note over UI,E: nothing has been mutated

    U->>UI: click Approve
    UI->>B: POST /api/approve/{run_id} {approve: true}
    B->>B: recover args from the run's ROUTING event
    B->>E: call_tool(same tool, same args, run_id + "-apply", approval_token)
    E->>E: hmac.compare_digest(token, expected)
    alt matches
        E->>E: codegen → sandbox → upstream
        E-->>B: 📤 code_generated · executing · result · done
    else mismatch
        E-->>B: 📤 error "approval token does not match this proposal"
    end
```

**The proposal preview shows the real request**, resolved through the contract's own
path mappings, with the wire names the upstream will see:

```
POST https://api.salla.dev/admin/v2/orders/777888/cancel
```

The approver should read what will actually be sent, not a paraphrase of it.

**Signing, and its POC limit.** `_APPROVAL_SECRET` is `secrets.token_hex(16)` minted
at import, so an "approved" write has to come from a proposal *this process* issued.
It is **process-local and lost on restart** — a restart between propose and apply
invalidates the token, which fails closed.

**A DELETE cannot opt out.** `needs_approval` returns `True` for `method == "DELETE"`
before it ever reads the governance block — not through the contract, and not by
arriving from a source with no gate in front of it.

---

## 9. Stage events

The executor emits stages as it works rather than returning one final blob. It never
knows how they travel: under FastMCP the sink forwards to `ctx.info(type, extra=payload)`
and they ride MCP log notifications; in tests the sink is a list.

### 9.1 The vocabulary — 11 types

| Event | Emitted by | Payload |
|---|---|---|
| `prompt_received` | BFF | `prompt` |
| `routing` | BFF | `chosen, rationale, candidates, args, source, usingLlm` |
| `contract_selected` | engine | `contractName, version, contract` *(the raw JSON)*, `uiHint` |
| `code_generated` | engine | `code` *(the real source)*, `fromCache, language, cacheKey` |
| `executing` | engine | `tool, binding, principal` |
| `result` | engine | `output, fromCache, uiHint` |
| `cache_store` | engine | `key, ttlSeconds` |
| `cache_hit` | engine | `key, storedAt, expiresAt` |
| `proposal` | engine | `contractName, action, args, approvalToken, reason` |
| `error` | either | `stage, message` |
| `done` | engine | `durationMs` + one of `cached` / `proposed` / `failed` |

`Emitter.emit` validates the type against `EVENT_TYPES` and raises on an unknown one —
so a typo fails at the emit site instead of showing up as a stage the UI silently
never renders, *which is exactly the sort of bug that survives to a demo*.

### 9.2 The envelope, and why it is nested

```json
{ "msg": "code_generated",
  "extra": { "stage_event": { "run_id": "run-8f2c…", "seq": 3, "ts": 1.7e9, "data": { … } } } }
```

`ctx.info(extra=…)` builds a stdlib `LogRecord`, which **raises on any key colliding
with a reserved attribute** — and two of our payloads do: `proposal` carries `args`,
every `error` carries `message`. Spreading the payload flat would make error reporting
the first thing to break. One wrapper key makes the whole class of collision
impossible, whatever a future event carries.

### 9.3 The journey to the browser

```mermaid
flowchart LR
    EX["Executor.emit(...)"] --> SINK["_ctx_sink<br/>ctx.info(type, extra=payload)"]
    SINK --> MCP(("MCP log<br/>notification"))
    MCP --> LH["McpBridge._log_handler<br/>StageEvent.from_notification"]
    LH --> RN["<b>renumber</b> event.seq = len(run.buffer)<br/><i>the engine's seq restarts at 0 per call;<br/>the BFF already stamped 2 events</i>"]
    RN --> Q["run.buffer.append + queue.put_nowait"]
    Q --> SSE["GET /api/stream/{run_id}<br/>replay the buffer, then follow live"]
    SSE --> ES["useEventStream.ts"]
    ES --> PP["PipelinePane.tsx"]

    style RN fill:#fef7e0,stroke:#fbbc04
```

Events for an unknown `run_id` are dropped, and a notification that is not one of ours
returns `None` from `from_notification` and is ignored — ordinary server logging passes
through harmlessly.

---

## 10. A run, end to end

`"list recent shipped orders"`, first call, `DEV_OFFLINE=0`.

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant UI as React
    participant B as BFF
    participant G as LangGraph
    participant E as FastMCP tool
    participant X as Executor
    participant CM as code_mode
    participant S as sandbox subprocess
    participant API as api.salla.dev

    U->>UI: prompt
    UI->>B: POST /api/chat
    B-->>UI: {run_id}  (queue already exists)
    UI->>B: GET /api/stream/{run_id}
    B->>B: 📤 seq 0 prompt_received
    B->>E: list_tools()
    E-->>B: the catalog + _meta (whenToUse, rules, dependencies, governance…)
    B->>G: route_prompt(prompt, catalog)
    G->>G: route → list_orders, args {status: ["shipped"]}
    G->>G: validate_args — array widening, regex rules, unknown args
    B->>B: 📤 seq 1 routing {chosen, rationale, candidates, source}
    B->>E: call_tool("list_orders", {status: ["shipped"], run_id})

    E->>X: executor.run(..., principal=_principal())
    X->>X: 📤 seq 2 contract_selected (raw contract JSON)
    X->>CM: generation_id(entry) → "a1b2c3d4e5f6"
    X->>X: cache_key(...) → miss
    X->>CM: generate(entry) → a self-contained module, ~10k chars
    X->>X: code_cache.put → .cache/code/list_orders__3.0.0__a1b2c3d4e5f6.py
    X->>X: 📤 seq 3 code_generated {code, fromCache: false}
    X->>X: 📤 seq 4 executing {tool, binding: http, principal}
    X->>S: run_module(path, args, secrets={SALLA_ACCESS_TOKEN: …})
    S->>S: validate(args)
    S->>API: GET /orders/statuses          ← the resolver
    API-->>S: data[] with this store's ids
    S->>S: status ["shipped"] → [1201821018]
    S->>API: GET /orders?status[]=1201821018
    API-->>S: {status, success, data: [...], pagination: {...}}
    S->>S: shape → {items: [...], count: 15, pagination: {...}}, each item projected
    S-->>X: stdout {"ok": true, "result": {...}}
    X->>X: 📤 seq 5 result {output, fromCache: false, uiHint: {display: table}}
    X->>X: result_cache.put(key, output, ttl=60)
    X->>X: 📤 seq 6 cache_store {key, ttlSeconds: 60}
    X->>X: 📤 seq 7 done {durationMs: 940, cached: false}
    E-->>B: {status: ok, output, cached: false, durationMs: 940}
    B-->>UI: SSE ×8 → table + live pipeline trace
```

Ask the same thing again inside 60s and the trace is `prompt_received · routing ·
contract_selected · cache_hit · result · done` — six events, single-digit
milliseconds, and the two missing ones are missing because they did not happen.

---

## 11. Failure taxonomy

Where a failure surfaces tells you which layer owns it.

```mermaid
flowchart TB
    subgraph L1["🔵 agent — before the engine is called"]
        A1["no tool fits → 'No contract in the registry fits this prompt.'"]
        A2["validation_errors → the specific schema/regex complaint"]
        A3["missing_args → '{tool} needs X, which the prompt does not give.'"]
    end
    subgraph L2["🟢 engine — registry layer"]
        B1["KeyError → 'no approved contract named X'"]
        B2["load-time warning → skipped, with the reason<br/><i>hash mismatch · unlisted · unconfigured api · no builtin</i>"]
        B3["UnsupportedRegistry → the whole registry refused"]
    end
    subgraph L3["🟡 engine — execution layer"]
        C1["code generation failed: {exc}   (stage=code_generated)"]
        C2["sandbox: start / timeout / exit / non-JSON / empty"]
        C3["approval token does not match this proposal"]
    end
    subgraph L4["🔴 generated module — the contract's own sentences"]
        D1["missing required argument: X"]
        D2["argument X is forbidden by this contract"]
        D3["a validation.rule message, verbatim"]
        D4["resolver: 'this store has no X. It has: …'"]
        D5["describe_failure: status + the contract's meaning +<br/>the upstream's message + invalid fields + retryable"]
        D6["'the response has no data -- the upstream envelope<br/>is not the one the contract declared'"]
    end

    L1 --> L2 --> L3 --> L4

    style L1 fill:#e8f0fe,stroke:#4285f4
    style L2 fill:#e6f4ea,stroke:#34a853
    style L3 fill:#fef7e0,stroke:#fbbc04
    style L4 fill:#fce8e6,stroke:#ea4335
```

Every one of them arrives as an `error` stage event carrying `stage` and `message`, so
the right pane names the layer that failed rather than showing a blank.

### 11.1 Quick diagnosis

| Symptom | Most likely cause |
|---|---|
| an answer that looks complete but ignores the filter | the classic — a filter value that never resolved. With v2 `resolve` + `onMiss: error` this now fails loudly instead |
| a stale answer after correcting a contract | should be impossible — `generation_id` covers content, not just `contractVersion`. If it happens, check whether the contract file was edited *without* rebuilding the registry |
| answers that look like a mock in production | `DEV_OFFLINE` — the generated module's `BASE_URL` names the host it really calls, so read the `code_generated` event |
| a tool visible in the contracts repo but not in the UI | it was never pinned; see [ci-cd-workflows.md § 9.2](ci-cd-workflows.md#92-the-two-places-a-contract-can-be-rejected) |
| `list_contracts().source.origin` says *(unapproved)* | the engine is running with `CM_REGISTRY_FILE` or `CM_CONTRACTS_DIR` set |
| a write that returned a proposal twice | the process restarted between propose and apply — `_APPROVAL_SECRET` is process-local, and it fails closed |

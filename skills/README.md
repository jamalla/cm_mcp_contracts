# Skills

Agent skills this repository ships. A skill is a file an agent loads **by itself** when the task
matches its `description` — the persistent form of a briefing you would otherwise hand over once
per endpoint, the way [docs/contributor-prompt.md](../docs/contributor-prompt.md) does.

| Skill | Loads when |
|---|---|
| [`salla-tool-contract/SKILL.md`](salla-tool-contract/SKILL.md) | the task is "add a contract for *endpoint*", "add a tool that lists/gets/creates X in Salla", or a contributor briefing lands in the conversation |

## Using one

Copy (or symlink) the skill's directory into a `.claude/skills/` your agent can see — inside your
clone, or at the root of a workspace holding it:

```bash
mkdir -p .claude/skills
cp -r skills/salla-tool-contract .claude/skills/
```

An agent working from that directory then loads it on its own; nothing has to be pasted into a
prompt. Keep the frontmatter's `name` and `description` if you adapt the rest — the `description`
is what the agent matches the task against, and the directory name must match `name`.

## What `salla-tool-contract` carries

The README's conventions, plus what a first submission finds the hard way:

- a briefing adapted from `contributor-prompt.md` often has prose that lags its GOAL — the path,
  tool name and docs link are the binding facts, a leftover endpoint title is not;
- the A2UI rule that fails silently: a leading `/` reads from the result, no leading slash reads
  from the current item inside a template, and getting it backwards renders every row blank;
- what the semantic gate checks that the schema cannot, and what the cross-reference checks reject;
- the research procedure — the docs page *and* the published OpenAPI spec — and the local commands
  for all three gate jobs before pushing.

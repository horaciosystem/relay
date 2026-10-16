# Relay

Speed-first multi-model orchestration for Claude Code.

`/relay:run <ticket | spec | plan file | description>`

The main session (Opus) acts as tech lead: it plans, hands the building to two parallel
lanes, reviews every diff, and runs the checks once at the end. It is built for speed once
the plan is approved.

## Who does what

| Role | Model | Job |
|---|---|---|
| Orchestrator | your session (Opus 5.5) | Plans, decides the shared types and signatures, reviews every diff, makes one-line fixes, runs the final checks |
| Scout (`relay-scout`) | Haiku 5.5 | Fast read-only recon: where code lives, which existing feature to copy, call sites |
| Plan reviewers | Codex and Fable 5.1, in parallel | Look for holes in the plan before anything is built. Read-only |
| Build lane A | Codex | Implementation slices with lots of mechanical code |
| Build lane B (`relay-builder-opus`) | Opus 5.5 | Slices that need judgment: tricky state, non-obvious integration |
| Last resort (`relay-solver`) | Fable 5.1 | A slice both lanes failed, or a bug nobody can reproduce |

Codex runs through OpenAI's [Codex plugin for Claude Code](https://github.com/openai/codex-plugin-cc)
(see Install). Without it, relay runs lane B only and uses Fable alone for plan review. To
pick a Codex model, name it in the task ("codex model gpt-6-astra"); relay passes it on
every Codex call.

`relay-builder` (Sonnet 5.5) is still shipped, but `/relay:run` no longer dispatches to it.

## The steps

**0. Size check.** If the task is about 1 to 4 files with no open design question, relay
skips all of this and just does the work.

**1. Recon.** It writes 2 to 5 sharp questions and sends them to Haiku scouts all at once.
Then the orchestrator reads the key files the scouts point to itself.

**2. Plan, then one gate.** It writes a short plan file with four parts:

- **Goal and acceptance:** what the user should see, in 3 to 5 lines.
- **Contracts:** the types, function signatures, routes and props that slices share,
  decided upfront so builders never have to.
- **Slices:** each one owns its own files (no overlap), names the existing code to copy,
  prefers a lane, and gets a time budget (S 5 min, M 12, L 20).
- **Verification:** the repo's real format, lint, typecheck and test commands.

Codex and Fable review the plan at the same time. Valid points get folded in; style
opinions are ignored. Then it shows you the plan and waits for "go". This is the only
planned stop, and it is skipped if you have already said to proceed without asking.

**3. Build: two lanes, one queue.**

- The orchestrator writes the shared types itself first, so both lanes compile against the
  same thing.
- Slices are queued in dependency order. A free lane takes the next ready slice instead of
  sitting idle.
- Codex gets a self-contained brief file, since it sees none of the conversation. Opus gets
  the plan and its slice.
- At most two heavy jobs run at once, to protect the machine.
- While the lanes build, the orchestrator reviews finished diffs and writes the next brief.
- **Handoff when a lane is slow:** if a slice runs past its budget, or takes twice as long
  as the other lane took for a similar slice, it is stopped. Its partial work goes to the
  free lane with a note on what is left. A slice is handed over at most once.

**4. Review each result.** The orchestrator reads every diff against the plan:

- A small problem it fixes itself.
- Wrong logic goes to the other lane, with what was wrong.
- A blocked builder gets a decision right away: change the plan, make the edit itself, or
  move the file to another slice.
- If both lanes fail, the slice goes to Fable. Only if Fable can't decide does it ask you
  one question.

**5. Verify once, then report.** When the queue is empty, it runs typecheck, lint and the
relevant tests once. Failures are fixed and only those checks rerun. The report covers:

- what each slice built, and which lane built it;
- what the plan reviewers caught;
- the check results;
- anything left open.

It never commits, pushes or opens a PR unless you ask.

## Guard rails

- **Context cap:** a PreToolUse hook stops scouts at 60k tokens and builders at 120k
  (`RELAY_SCOUT_CAP`, `RELAY_BUILDER_CAP`). Past that, the agent has to hand off instead of
  drifting.
- **Ownership:** builders format and lint only their own files. The orchestrator owns the
  contracts, the reviews and the final checks.

## A real run

A refactor that moved a details page onto a new store, copying a sibling module that
already had it:

- Haiku scouts mapped the page against the sibling module.
- Codex and Fable reviewed the plan, and the user said "go".
- Codex and Opus built the slices in parallel.
- The orchestrator reviewed the diffs, then ran the full checks, E2E tests and a smoke test.

The PR was open after about 44 minutes of working time.

## Is it helping?

Relay measures itself from your local transcripts (no extra tokens). Each `/relay:run`
window is compared with plain Opus sessions that changed a similar number of files:
working minutes per file changed, your prompts, tokens by model, escalations, and
whether verification was green on the first try.

- End of every relay run: a two-line comparison.
- End of every session: `~/.claude/relay-stats/report.html` is regenerated in the background.
- Start of a session after 3+ new runs: Claude offers the report.
- `/relay:report`: builds the scorecard and publishes it as an artifact (same link every time).

Tracking starts the first time the stats run. Data lives in `~/.claude/relay-stats/`.

## Install

In Claude Code:

```
/plugin marketplace add horaciosystem/relay
/plugin install relay@relay
/reload-plugins
```

`/relay:run` and `/relay:report` are now available in every project.

**Optional, for the Codex lane:** install OpenAI's
[Codex plugin for Claude Code](https://github.com/openai/codex-plugin-cc). Without it,
relay still works with the Opus lane only. The Codex plugin needs the Codex CLI signed in
to a ChatGPT or OpenAI account:

```
/plugin marketplace add openai/codex-plugin-cc
/plugin install codex@openai-codex
/reload-plugins
/codex:setup
```

`/codex:setup` checks the Codex CLI and offers to install it (`npm install -g @openai/codex`).
If it says you are not signed in, run `!codex login`. Relay finds the plugin at
`~/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs`.

**Updating:** `/plugin marketplace update relay`, then `/reload-plugins`.

**Working on relay itself:** clone this repo and start Claude Code with your copy loaded for
that session: `claude --plugin-dir <path to the clone>`.

## Check

```sh
claude plugin validate --strict .
python3 -m py_compile hooks/context-cap.py scripts/stats.py
```

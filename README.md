# Relay

Speed-first multi-model orchestration for Claude Code.

`/relay:run <ticket | spec | plan file | description>`

The main session acts as tech lead: it plans, hands the building to two parallel lanes,
reviews every diff, and runs the checks once at the end. The target is a normal feature or
multi-file change in **15 to 20 minutes** from the request to a verified diff (smoke tests and
PRs not included).

## Who does what

| Role | Model | Job |
|---|---|---|
| Orchestrator | your session | Plans, fixes the shared types and signatures, writes the slice briefs, reviews every diff, verifies |
| Scout (`relay-scout`) | Haiku | Read-only recon: where code lives, the sibling to copy, call sites |
| Plan reviewers | Codex and Fable, in parallel | Concrete defects in the plan before anything is built. Read-only |
| Build lane A | Codex | Slices with lots of mechanical code: wiring, CRUD, renames, tests |
| Build lane B (`relay-builder-opus`) | Opus | Slices that need judgment: state, integration |
| Last resort (`relay-solver`) | Fable | A slice both lanes failed, or a bug nobody can reproduce |

The agents use the `haiku`, `opus` and `fable` aliases, so they run whatever version your
Claude Code resolves those to. Codex runs through OpenAI's
[Codex plugin for Claude Code](https://github.com/openai/codex-plugin-cc) (see Install), with
the model in your `~/.codex/config.toml` unless the task names one ("codex model X"). Without
the Codex plugin, relay runs the Opus lane only and Fable reviews the plan alone.

## The clock

| Phase | Ends by | What happens |
|---|---|---|
| 0 Setup | 1 min | Finds the Codex plugin, snapshots the working tree, looks up the repo's check commands. A task of 4 files or fewer with no open design question is done directly, without orchestration |
| 1 Recon | 3 min | Haiku scouts, only for what a few greps can't answer |
| 2 Plan | 6 min | A plan file with goal, acceptance checks, contracts and 1-4 slices. Codex and Fable review it (only for 3+ slices or cross-module contracts), with 4 minutes to answer. Then the gate, which waits only when there is a product question |
| 3 Build | 15 min | Both lanes build from one queue |
| 4 Verify | 20 min | Typecheck, lint and the tests for the changed code, once |

When it falls behind the clock, relay cuts scope and names what it cut in the report. It never
skips reviewing a diff.

## How a run works

**Fewer questions.** Before asking anything, relay sorts the question:

- a fact it could observe (what the code does, an output, whether a test passes): it finds out;
- a reversible choice the repo answers (naming, placement, test shape): it follows the repo;
- a product call no experiment can settle: it asks once, at the plan gate, with its default.

If you said to proceed or to work non stop, or the question comes up mid-build, relay applies
the default and keeps going. Every default and judgment call is logged and listed in the final
report, with what you could ask for instead. It pauses only for irreversible actions
(force-push, deploys, deleting data, messages to people).

**Plan.** Done is a checkable predicate: ACCEPT lists commands with their expected results,
and relay never weakens it, a test or an assertion to reach green; it cuts scope and says so.
Every slice owns its own files (no overlap), names the existing code to copy, has a lane, a
size and a budget (S 4 min, M 8, L 12), and one targeted check. Coupled work (one state model,
one migration) stays one slice on the Opus lane, and the plan says why; splitting it would only
add handoffs.

**The gate.** Relay shows the plan and waits only when there is a product question, or when you
asked to approve the plan. Otherwise it goes straight to the build.

**Briefs written for models.** Each slice gets a brief file in a fixed shape: TASK, REPO, OWN,
READ, COPY, CONTRACT, DO, DONE WHEN, CHECK, NEVER, and an END WITH block the builder fills in
(STATUS, FILES, CHECK, DECISIONS, BLOCKER). Paths and facts only, no narrative. Both lanes get
the same brief and report in the same shape, so the orchestrator reads results the same way.

**Build.**

- The orchestrator writes the shared types itself first, so both lanes compile against them.
- A free lane takes the next ready slice instead of idling. At most two heavy jobs run at
  once: the two lanes.
- **Handoff:** a slice past its budget, or at 2× the other lane's time for the same size, is
  stopped. Its partial work is kept or reverted and goes to the free lane with a one-line note.
  One handoff per slice.

**Your edits are safe.** Before building, relay snapshots the working tree with
`git stash create` (which changes nothing) and never gives a slice a file you had already
changed. Reverts restore only that slice's files from the snapshot; relay never runs
`git checkout`, `git restore` or `git clean`.

**Review.** Every result is read against the brief: the diff from the snapshot, new files, and
anything touched outside the slice. Small fixes the orchestrator makes itself; wrong logic goes
to the other lane. When two attempts fail the same check, relay writes down the assumption both
shared and tests it before a third try. A slice both lanes fail goes to Fable, with that test.

**No invented facts.** Builders grep a symbol, flag or API before using it, and report the
check they actually ran with its real result. Scouts cite only lines they opened. In the final
report every claim is marked measured (with the command), inferred or guess, and relay never
hands you a check it could run itself.

**Verify and report.** One run of the full checks and the ACCEPT predicate, then a report of
what each slice built and on which lane, what the reviewers caught, the check results, the
decisions taken and what is left open. It never
commits, pushes or opens a PR unless you ask.

## Guard rails

- **Context cap:** a PreToolUse hook stops scouts at 60k tokens and builders at 120k
  (`RELAY_SCOUT_CAP`, `RELAY_BUILDER_CAP`), and they report what is done and left. A shell
  check skips everything that isn't a relay agent, so it costs a few milliseconds per tool call.
- **Ownership:** builders run format, lint and one targeted test on their own files. The
  orchestrator owns the contracts, the reviews and the full checks.

## Is it helping?

Relay measures itself from your local transcripts (no extra tokens). Each `/relay:run`
window is compared with plain Opus sessions that changed a similar number of files:
working minutes per file changed, your prompts, tokens by model, escalations, and
whether verification was green on the first try.

- End of every relay run: a two-line comparison.
- End of every session: `~/.claude/relay-stats/report.html` is regenerated in the background.
- Start of a session after 3+ new runs: Claude offers the report.
- `/relay:report`: builds the scorecard and publishes it as an artifact (same link every time).

Known limits: time and tokens spent inside Codex are not counted yet, and a run's window lasts
until the next `/relay:run` or the end of the session, so chat after a run counts toward it.
Data lives in `~/.claude/relay-stats/`.

## Install

Needs `git` and `python3` (for the hook and the stats; without `python3` they switch off).

In Claude Code:

```
/plugin marketplace add horaciosystem/relay
/plugin install relay@relay
/reload-plugins
```

`/relay:run` and `/relay:report` are now available in every project.

**Optional, for the Codex lane:** install OpenAI's
[Codex plugin for Claude Code](https://github.com/openai/codex-plugin-cc). It needs the Codex
CLI signed in to a ChatGPT or OpenAI account:

```
/plugin marketplace add openai/codex-plugin-cc
/plugin install codex@openai-codex
/reload-plugins
/codex:setup
```

`/codex:setup` checks the Codex CLI and offers to install it (`npm install -g @openai/codex`).
If it says you are not signed in, run `!codex login`. Relay looks for the plugin under
`~/.claude/plugins/cache/openai-codex/`; set `RELAY_CODEX_COMPANION` to the path of
`codex-companion.mjs` if yours is elsewhere.

**Updating:** `/plugin marketplace update relay`, then `/reload-plugins`.

**Working on relay itself:** clone this repo and start Claude Code with your copy loaded for
that session: `claude --plugin-dir <path to the clone>`.

## Check

```sh
claude plugin validate --strict .
python3 -m py_compile hooks/context-cap.py scripts/stats.py
```

## License

MIT

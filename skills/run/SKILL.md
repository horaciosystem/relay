---
name: run
description: Speed-first multi-model orchestration for one task, aimed at 15-20 minutes end to end. The main session plans and reviews; Codex and Fable review the plan; Codex and Opus build in two parallel lanes with handoff when a lane is slow; Haiku scouts do recon; Fable is the last resort. Asks only product calls no experiment can settle; logs defaults and reports them at the end. Use for a feature, ticket or multi-file change; skip it for a fix you can make yourself in a few edits.
---

You orchestrate. You plan, write the briefs, review every diff, and verify once. Lanes build. Target: 15-20 min wall clock from input to verified diff (smoke tests and PRs excluded).

Input: $ARGUMENTS (ticket, spec, plan file or description). A named Codex model ("codex model X") → `--model X` on every Codex call.

## Clock

| Phase | Ends by | Hard rule |
|---|---|---|
| 0 Setup + size | 1 min | ≤4 files and no open design question → do it yourself, stop following this skill |
| 1 Recon | 3 min | Skip scouts when 3 greps answer it |
| 2 Plan + review | 6 min | Reviewers get 4 min, then proceed with what returned |
| 3 Build | 15 min | 1-4 slices; lanes busy; slow slice → handoff |
| 4 Verify + report | 20 min | One full check run; fix only failures |

Behind the clock → cut scope you name in the report. Never skip reviewing a diff.

## Questions and decisions

Classify before asking anything:

| The question is | Do |
|---|---|
| A fact you could observe: what code does, a type, an output, timing, whether a test passes | Find out: grep, read, run it. Never ask |
| A reversible choice the repo answers: naming, placement, error handling, test shape | Follow the sibling or the repo convention. Never ask |
| A product or preference call no experiment can settle, that changes what gets built | Ask at the gate: one line, with your default. Gate skipped or mid-build → apply the default and log it |

- Log every default and every judgment call to `$RUN/decisions.md`: `<decision> | why | what the user could ask for instead`. Report them all at the end.
- Mid-build, never stop for a decision: log it and continue.
- Pause only for irreversible actions: force-push, deploy, deleting data, messages to people.

## 0. Setup

```bash
RUN=${TMPDIR:-/tmp}/relay-${CLAUDE_CODE_SESSION_ID:-$$}; mkdir -p "$RUN"
CX=${RELAY_CODEX_COMPANION:-$(ls -d "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs 2>/dev/null | sort -V | tail -1)}
[ -f "$CX" ] && echo "codex: $CX" || echo "codex: none"
BASE=$(git stash create); BASE=${BASE:-HEAD}; echo "$BASE" > "$RUN/base"; git status --porcelain > "$RUN/status-before"
```

Shell variables do not survive between Bash calls: read `$RUN/base` back (`BASE=$(cat "$RUN/base")`) and repeat the `CX=` line where needed.

- `codex: none` → the Codex plugin is missing: no Codex lane, Fable reviews alone, the Opus lane takes every slice.
- `BASE` snapshots the working tree without touching it. Reverts and review diffs use it, so edits that existed before the run are never lost.
- Never give a slice a file listed in `status-before`. If the task needs one, edit it yourself.
- Look up once and keep: the repo's format, lint, typecheck and test commands (package scripts, Makefile, CI config).

## 1. Recon

Only for what 3 greps can't answer. 2-4 `relay-scout` agents in ONE message, one question each:

```
Q: <one question>
SCOPE: <dirs to search>
RETURN: paths with line ranges; the closest existing sibling to copy
```

Read the files they point to yourself. Never rerun their searches.

## 2. Plan

Write `$RUN/plan.md`:

```
GOAL: <observable behaviour, 1-3 lines>
ACCEPT: <checkable predicate: command -> expected result, one per line>
SPLIT: <why this many slices; "1: coupled, <reason>" is valid>
CONTRACTS: <exact types/signatures/routes/props shared across slices, as code>
SLICES:
  S1 lane=codex|opus size=S|M|L hard=no own=<paths, new-file dirs> copy=<path:lines>
     do: <imperative steps>
     check: <format+lint on own files>; <one targeted test command>
  S2 ...
ORDER: <S1 -> S3; S2 independent>
VERIFY: <typecheck cmd>; <lint cmd>; <tests for changed code>
```

Rules: 1-4 slices, split only at real seams, `own` never overlaps. Coupled work (one state model, one migration, files that must change together) is one slice on the Opus lane; the Codex lane may take an independent slice such as tests against the contract. `lane=codex` for volume (wiring, CRUD, renames, tests), `lane=opus` for judgment (state, integration). Budgets: S 4 min, M 8 min, L 12 min.

**Review** only when slices ≥ 3 or contracts cross modules. Both in ONE message, both in the background:

- Codex: `$RUN/review.md` = the review line + the plan. `node "$CX" task --prompt-file "$RUN/review.md" --effort medium [--model M]` (Bash, `run_in_background: true`).
- Fable: Agent, `subagent_type: "Plan"`, `model: "fable"`, the review line + the plan path.

Review line: `Review this plan against the repo. Read-only. Output only concrete defects as "path:line | defect | fix", max 8: wrong or missing files, missed call sites, broken contracts, missing slices, wrong commands. No style.`

At 4 min, use what has returned. Fold valid defects into the plan; drop style.

**Gate.** Show the slices, one line per reviewer change, and the product questions with your defaults.

- Product questions and the user has not said to proceed → wait for answers. This is the only stop.
- No product question, or the user said to proceed or to work non stop → show the plan and continue without waiting; defaults go to `$RUN/decisions.md`.
- The user asked to approve the plan → wait for "go".

## 3. Build

After the gate, in this order:

1. Write the CONTRACTS code yourself (types, signatures, stubs), so both lanes compile against it.
2. Write one brief per slice, `$RUN/S<n>.md`, in exactly this shape. Facts and paths only, no narrative: the builder sees nothing else.

```
TASK: <one imperative sentence>
REPO: <absolute path>
OWN: <files and new-file dirs; edit nothing else>
READ: <path:lines | why>  (max 6)
COPY: <path:lines of the pattern to match>
CONTRACT (fixed, do not change):
<code>
DO:
1. ...
DONE WHEN: <checkable conditions>
CHECK: <format+lint command on OWN files>; <one targeted test command>
NEVER: full test suite, typecheck, build, dev server, new dependencies, edits outside OWN, refactors outside the task, questions, weakening or deleting a test or assertion to pass, symbols or flags you have not seen in the code or docs
END WITH:
STATUS: DONE|BLOCKED|FAILED
FILES: <paths>
CHECK: <command actually run -> real result>
DECISIONS: <choices the brief did not make, or none>
BLOCKER: <path:line + what is needed, or none>
```

3. Dispatch from the queue. Each lane holds one slice; a free lane takes any ready slice before idling.
   - **Codex lane:** `node "$CX" task --background --write --prompt-file "$RUN/S<n>.md" --effort medium --json [--model M]` (`--effort high` when `hard=yes`). Save `jobId` from the JSON and the start time.
     Wait: `node "$CX" status <jobId> --wait --timeout-ms <budget min × 60000> --json` (Bash, `run_in_background: true`).
     - `job.status` is no longer queued or running → `node "$CX" result <jobId>`.
     - `waitTimedOut: true` → the slice is slow: apply Handoff. Never call `result` on a running job.
   - **Opus lane:** Agent `relay-builder-opus`, `run_in_background: true`, prompt `Brief: <absolute path of S<n>.md>`. Save the start time.
4. At most 2 heavy jobs at once: the two lanes. While they run, review finished slices and write the next brief.

**Handoff.** Slow = past budget, or 2× the other lane's time on a slice of the same size. When a slice is slow and the other lane is free with nothing ready:

1. Stop it: `node "$CX" cancel <jobId>` or `TaskStop`.
2. Read the partial work: `git diff "$BASE" -- <own>` plus new files in `git status --porcelain -- <own>`.
3. Keep or revert. Revert only that slice's files, from the repo root: `git show "$BASE:<file>" > <file>` for a file in BASE, `rm <file>` for a file the slice created. Never `git checkout`, `git restore`, `git stash` (other than `create`) or `git clean`.
4. Dispatch to the other lane: the brief plus `PARTIAL: kept|reverted; LEFT: <one line>`.

One handoff per slice. If the second lane is slow too, let it finish.

## 4. Review each slice

Diff = `git diff "$BASE" -- <own>` + every new file in `git status --porcelain -- <own>` + anything changed outside OWN (`git status --porcelain` against `$RUN/status-before`).

- Matches the brief and contracts → accept, and copy its DECISIONS into `$RUN/decisions.md`. A 1-2 line fix → make it yourself.
- Edits outside OWN → revert them as in Handoff, or keep and own them yourself when needed.
- Wrong logic or FAILED → the other lane, with the diff and `WRONG: <one line>`.
- BLOCKED → decide now: change the plan or contract, make the seam edit yourself, or move the file. New brief, fresh agent or task.
- CHECK reported as pass → rerun it yourself before accepting when the diff is not obviously right.
- Two attempts fail the same check → attack the premise: write in one line the assumption both attempts shared, and test it directly (run, read, print) before any third attempt.
- Failed on both lanes, or a bug with no repro → `relay-solver` (Fable) with the plan, both diffs, the failing output and the premise test. `NEEDS_USER` → ask that one question.

Every attempt is a fresh agent or Codex task.

## 5. Verify, report, record

Queue empty and lanes idle → run VERIFY and every ACCEPT check once yourself. Fix failures with a small edit or one fresh slice; rerun only what failed. Never weaken ACCEPT, a test or an assertion to get green: cut scope instead and name it.

Report:

- per slice: what it built, the lane, any handoff;
- what the reviewers caught;
- VERIFY and ACCEPT commands with their real results;
- decisions taken, from `$RUN/decisions.md`: each with why and what the user could ask for instead;
- scope cut and anything left open.

Every claim carries its evidence in the same line: measured (the command and its result), inferred (from what), or guess. No link, path or reference you did not open this run. Never hand the user a check you could run.

Record (handoffs and solver calls count as escalations):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stats.py" record --session "$CLAUDE_CODE_SESSION_ID" \
  --task "<ticket id + few words>" --slices <n> --escalations <n> \
  --verify first|after-fix|failed --outcome done|partial|abandoned --files <n changed>
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stats.py" summary --session "$CLAUDE_CODE_SESSION_ID"
```

`--verify first` = VERIFY green on the first run. End the report with the two lines `summary` prints.

No commit, push or PR unless asked. Before reporting, cancel any Codex job still running (`node "$CX" status`) and stop any server, runner or browser you started.

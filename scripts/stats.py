#!/usr/bin/env python3
"""Relay stats: compares /relay:run windows against plain Opus sessions, from local transcripts.

Commands:
  scan                 refresh the cache from ~/.claude/projects
  record --session ID [--task T] [--slices N] [--escalations N]
         [--verify first|after-fix|failed] [--outcome done|partial|abandoned]
                       append the orchestrator's own outcome for the latest relay run
  summary [--session ID]   one-line comparison for the latest relay run
  report [--quiet]     scan, write report.html, print its path and the headline
  nudge                SessionStart hook: suggest /relay:report after 3+ new runs
  set-url URL          remember the published report artifact URL
"""
import argparse, glob, html, json, os, statistics, sys, tempfile, time
from collections import Counter, defaultdict
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
PROJECTS = os.environ.get("RELAY_PROJECTS_DIR", os.path.join(HOME, ".claude", "projects"))
DIR = os.environ.get("RELAY_STATS_DIR", os.path.join(HOME, ".claude", "relay-stats"))
CACHE, STATE, LEDGER = (os.path.join(DIR, n) for n in ("cache.json", "state.json", "runs.jsonl"))
REPORT = os.path.join(DIR, "report.html")
SKIP_PREFIXES = ("/tmp/", "/private/tmp/", "/var/folders/", os.path.join(HOME, ".claude") + "/")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
BUCKETS = [("S", "Small (1-4 files)", 1, 4), ("M", "Medium (5-9 files)", 5, 9), ("L", "Large (10+ files)", 10, 10**9)]
MIN_RELAY, MIN_BASE = 3, 5
PARSER_VERSION = 2


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, data):
    os.makedirs(DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DIR, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def short_model(m):
    m = (m or "").lower()
    for k in ("fable", "opus", "sonnet", "haiku"):
        if k in m:
            return k
    return "unknown"


def lines(path):
    with open(path, errors="replace") as f:
        for line in f:
            if '"api-request' in line[:40]:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def is_relay_start(d):
    if d.get("type") == "user":
        c = d.get("message", {}).get("content")
        return isinstance(c, str) and "<command-name>/relay:run" in c
    if d.get("type") == "assistant":
        for c in d.get("message", {}).get("content") or []:
            if c.get("type") == "tool_use" and c.get("name") == "Skill" and str(c.get("input", {}).get("skill", "")).endswith("relay:run"):
                return True
    return False


def is_human(d):
    if d.get("type") != "user" or d.get("isMeta") or d.get("isSidechain"):
        return False
    if "turnOrigin" in d:
        return d["turnOrigin"] == "human"
    c = d.get("message", {}).get("content")
    return isinstance(c, str) and not c.startswith("<local-command")


def counted_path(p):
    return bool(p) and not p.startswith(SKIP_PREFIXES)


def parse_session(path):
    """Return segments: relay windows plus the non-relay part of the session."""
    sid = os.path.basename(path)[:-6]
    events, agents_by_tool, seen_msgs = [], {}, set()
    cwd, models = None, Counter()
    for d in lines(path):
        t = ts(d.get("timestamp"))
        cwd = cwd or d.get("cwd")
        if is_relay_start(d) and t:
            events.append((t, "relay", None))
        if is_human(d) and t:
            events.append((t, "human", None))
        if d.get("type") == "system" and d.get("subtype") == "turn_duration" and t:
            events.append((t, "work", d.get("durationMs", 0)))
        if d.get("type") == "assistant" and not d.get("isSidechain") and t:
            m = d.get("message", {})
            model = short_model(d.get("requestedModel") or m.get("model"))
            if model != "unknown":
                models[model] += 1
            if not m.get("id") or m["id"] not in seen_msgs:
                seen_msgs.add(m.get("id"))
                events.append((t, "tokens", (model, m.get("usage") or {})))
            for c in m.get("content") or []:
                if c.get("type") != "tool_use":
                    continue
                if c.get("name") in EDIT_TOOLS:
                    events.append((t, "edit", c.get("input", {}).get("file_path") or c.get("input", {}).get("notebook_path")))
                elif c.get("name") in ("Agent", "Task"):
                    agents_by_tool[c.get("id")] = (t, c.get("input", {}).get("subagent_type") or "general-purpose")
    sub_dir = os.path.join(path[:-6], "subagents")
    for f in glob.glob(os.path.join(sub_dir, "agent-*.jsonl")):
        meta = load(f[:-6] + ".meta.json", {})
        spawn = agents_by_tool.get(meta.get("toolUseId"))
        first_t = None
        for d in lines(f):
            t = ts(d.get("timestamp"))
            if t is None:
                continue
            first_t = first_t or t
            at = spawn[0] if spawn else first_t
            if d.get("type") == "assistant":
                m = d.get("message", {})
                if not m.get("id") or m["id"] not in seen_msgs:
                    seen_msgs.add(m.get("id"))
                    events.append((at, "tokens", (short_model(d.get("requestedModel") or m.get("model")), m.get("usage") or {})))
                for c in m.get("content") or []:
                    if c.get("type") == "tool_use" and c.get("name") in EDIT_TOOLS:
                        events.append((at, "edit", c.get("input", {}).get("file_path")))
        if first_t:
            events.append((spawn[0] if spawn else first_t, "agent", (meta.get("agentType") or (spawn[1] if spawn else "?")).split(":")[-1]))
    events.sort(key=lambda e: e[0])
    starts = [e[0] for e in events if e[1] == "relay"]
    main_model = models.most_common(1)[0][0] if models else "unknown"

    def window(lo, hi, relay):
        seg = {"session": sid, "cwd": cwd, "main_model": main_model, "relay": relay, "start": None, "end": None,
               "work_ms": 0, "human_turns": 0, "files": set(), "tokens": defaultdict(lambda: [0, 0]), "agents": Counter()}
        for t, kind, val in events:
            if not (lo <= t < hi) or kind == "relay":
                continue
            seg["start"] = seg["start"] or t
            seg["end"] = t
            if kind == "work":
                seg["work_ms"] += val or 0
            elif kind == "human":
                seg["human_turns"] += 1
            elif kind == "edit" and counted_path(val):
                seg["files"].add(val)
            elif kind == "agent":
                seg["agents"][val] += 1
            elif kind == "tokens":
                model, u = val
                if model == "unknown":
                    continue
                seg["tokens"][model][0] += sum(u.get(k, 0) or 0 for k in ("input_tokens", "cache_creation_input_tokens"))
                seg["tokens"][model][1] += u.get("output_tokens", 0) or 0
        seg["files"] = len(seg["files"])
        seg["tokens"] = dict(seg["tokens"])
        seg["agents"] = dict(seg["agents"])
        seg["escalations"] = seg["agents"].get("relay-solver", 0)
        if relay:
            seg["start"] = lo
        return seg

    segs = []
    if not starts:
        segs.append(window(0, float("inf"), False))
    else:
        segs.append(window(0, starts[0], False))
        bounds = starts + [float("inf")]
        segs += [window(bounds[i], bounds[i + 1], True) for i in range(len(starts))]
    return [s for s in segs if s["start"] and (s["relay"] or s["files"] > 0)]


def scan():
    state = load(STATE, {})
    if "since" not in state:
        state["since"] = time.time()
        save(STATE, state)
    since = state["since"]
    cache = load(CACHE, {})
    seen = set()
    for path in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_mtime < since:
            continue
        subs = glob.glob(os.path.join(path[:-6], "subagents", "agent-*.jsonl"))
        sig = [PARSER_VERSION, st.st_mtime, st.st_size, len(subs), max((os.path.getmtime(s) for s in subs), default=0)]
        seen.add(path)
        if cache.get(path, {}).get("sig") == sig:
            continue
        try:
            cache[path] = {"sig": sig, "segments": parse_session(path)}
        except Exception:
            continue
    cache = {k: v for k, v in cache.items() if k in seen}
    save(CACHE, cache)
    segs = [s for v in cache.values() for s in v["segments"] if s["start"] >= since or s["relay"]]
    return segs, state


def ledger():
    out = []
    try:
        with open(LEDGER) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def attach_ledger(segs):
    by_session = defaultdict(list)
    for r in ledger():
        by_session[r.get("session")].append(r)
    starts = defaultdict(list)
    for s in segs:
        if s["relay"]:
            starts[s["session"]].append(s["start"])
    for s in segs:
        if not s["relay"]:
            continue
        nxt = min((t for t in starts[s["session"]] if t > s["start"]), default=float("inf"))
        recs = [r for r in by_session.get(s["session"], []) if s["start"] - 1 <= r.get("ts", 0) < nxt]
        s["record"] = min(recs, key=lambda r: r["ts"]) if recs else None
        if s["record"] and (s["record"].get("files") or 0) > s["files"]:
            s["files"] = s["record"]["files"]
        if s["record"] and s["record"].get("escalations") is not None:
            s["escalations"] = s["record"]["escalations"]
    return segs


def bucket(files):
    for key, label, lo, hi in BUCKETS:
        if lo <= files <= hi:
            return key
    return None


def total_tokens(s):
    return sum(i + o for i, o in s["tokens"].values())


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def per_file_min(s):
    return (s["work_ms"] / 60000) / s["files"] if s["files"] else None


def compare(segs):
    relay = [s for s in segs if s["relay"] and s["files"] > 0]
    base = [s for s in segs if not s["relay"] and s["main_model"] == "opus" and s["files"] > 0]
    rows = []
    for key, label, lo, hi in BUCKETS:
        r = [s for s in relay if bucket(s["files"]) == key]
        b = [s for s in base if bucket(s["files"]) == key]
        row = {"key": key, "label": label, "n_relay": len(r), "n_base": len(b)}
        for name, fn in (("min_per_file", per_file_min), ("work_min", lambda s: s["work_ms"] / 60000),
                         ("human_turns", lambda s: s["human_turns"]), ("tokens", total_tokens)):
            row[name] = (med(map(fn, r)), med(map(fn, b)))
        rm, bm = row["min_per_file"]
        if len(r) >= MIN_RELAY and len(b) >= MIN_BASE and rm and bm:
            row["delta"] = (bm - rm) / bm
            row["verdict"] = "faster" if row["delta"] >= 0.1 else "slower" if row["delta"] <= -0.1 else "same"
        else:
            row["delta"], row["verdict"] = None, "early"
        rows.append(row)
    return rows, relay, base


def headline(rows, relay, base):
    decided = [r for r in rows if r["verdict"] != "early"]
    if not decided:
        return (f"Too early: {len(relay)} relay run(s) and {len(base)} plain Opus session(s) with code changes. "
                f"A verdict needs {MIN_RELAY}+ relay runs and {MIN_BASE}+ Opus sessions in the same size bucket.")
    parts = []
    for r in decided:
        pct = abs(r["delta"]) * 100
        word = {"faster": f"{pct:.0f}% faster", "slower": f"{pct:.0f}% slower", "same": "about the same speed"}[r["verdict"]]
        parts.append(f"{r['label']}: relay is {word} per file changed (n={r['n_relay']} vs {r['n_base']})")
    return "; ".join(parts) + "."


def fmt_min(x):
    return "–" if x is None else f"{x:.1f}"


def fmt_tok(x):
    if x is None:
        return "–"
    return f"{x / 1e6:.2f}M" if x >= 1e6 else f"{x / 1e3:.0f}k"


def cmd_record(a):
    os.makedirs(DIR, exist_ok=True)
    rec = {"ts": time.time(), "session": a.session, "task": a.task, "slices": a.slices,
           "escalations": a.escalations, "verify": a.verify, "outcome": a.outcome, "files": a.files}
    with open(LEDGER, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print("recorded")


def cmd_summary(a):
    segs, _ = scan()
    segs = attach_ledger(segs)
    rows, relay, base = compare(segs)
    mine = [s for s in relay if not a.session or s["session"] == a.session]
    if mine:
        s = max(mine, key=lambda s: s["start"])
        b = next(r for r in rows if r["key"] == bucket(s["files"]))
        print(f"This relay run: {s['work_ms'] / 60000:.1f} working min, {s['files']} files, "
              f"{fmt_min(per_file_min(s))} min/file, {s['human_turns']} prompts from you, {s['escalations']} escalation(s). "
              f"Plain Opus median for {b['label'].lower()}: {fmt_min(b['min_per_file'][1])} min/file (n={b['n_base']}).")
    print(headline(rows, relay, base))


def cmd_nudge(_):
    segs, state = scan()
    n = len(compare(attach_ledger(segs))[1])
    new = n - state.get("reported_runs", 0)
    if new >= 3:
        msg = (f"relay-stats: {new} new /relay:run run(s) since the last report. At a natural pause, "
               "tell the user in one line and offer to run /relay:report.")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": msg}}))


def cmd_set_url(a):
    state = load(STATE, {})
    state["artifact_url"] = a.url
    save(STATE, state)
    print("saved")


def cmd_report(a):
    segs, state = scan()
    segs = attach_ledger(segs)
    rows, relay, base = compare(segs)
    head = headline(rows, relay, base)
    with open(REPORT, "w") as f:
        f.write(render(rows, relay, base, head, state))
    if not a.quiet:
        state["reported_runs"] = len(relay)
        state["last_report"] = time.time()
        save(STATE, state)
        print(json.dumps({"report": REPORT, "artifact_url": state.get("artifact_url"), "headline": head,
                          "relay_runs": len(relay), "opus_sessions": len(base)}, indent=2))


def day(t):
    return datetime.fromtimestamp(t).strftime("%b %d %H:%M")


def project(cwd):
    return html.escape(os.path.basename(cwd or "") or "?")


def render(rows, relay, base, head, state):
    e = html.escape
    pill = {"faster": ("good", "Relay faster"), "slower": ("bad", "Relay slower"),
            "same": ("neutral", "No difference"), "early": ("muted", "Too early")}
    cmp_rows = ""
    for r in rows:
        cls, text = pill[r["verdict"]]
        delta = "" if r["delta"] is None else f" {r['delta'] * 100:+.0f}%"
        cmp_rows += (f"<tr><th scope=row>{e(r['label'])}</th><td><span class='pill {cls}'>{text}{delta}</span></td>"
                     f"<td class=num>{r['n_relay']} / {r['n_base']}</td>"
                     f"<td class=num>{fmt_min(r['min_per_file'][0])} / {fmt_min(r['min_per_file'][1])}</td>"
                     f"<td class=num>{fmt_min(r['work_min'][0])} / {fmt_min(r['work_min'][1])}</td>"
                     f"<td class=num>{fmt_min(r['human_turns'][0])} / {fmt_min(r['human_turns'][1])}</td>"
                     f"<td class=num>{fmt_tok(r['tokens'][0])} / {fmt_tok(r['tokens'][1])}</td></tr>")
    run_rows = ""
    for s in sorted(relay, key=lambda s: -s["start"]):
        rec = s.get("record") or {}
        agents = ", ".join(f"{k.replace('relay-', '')}×{v}" for k, v in sorted(s["agents"].items())) or "none"
        mix = ", ".join(f"{m} {fmt_tok(i + o)}" for m, (i, o) in sorted(s["tokens"].items(), key=lambda kv: -sum(kv[1])))
        verify = rec.get("verify") or "–"
        vcls = {"first": "good", "after-fix": "neutral", "failed": "bad"}.get(verify, "muted")
        run_rows += (f"<tr><td class=num>{day(s['start'])}</td><td>{project(s['cwd'])}</td>"
                     f"<td class=task>{e(rec.get('task') or '–')}</td><td class=num>{s['files']}</td>"
                     f"<td class=num>{s['work_ms'] / 60000:.1f}</td><td class=num>{fmt_min(per_file_min(s))}</td>"
                     f"<td class=num>{s['human_turns']}</td><td>{e(agents)}</td><td class=num>{s['escalations']}</td>"
                     f"<td><span class='pill {vcls}'>{e(verify)}</span></td><td class=mix>{e(mix)}</td></tr>")
    if not run_rows:
        run_rows = "<tr><td colspan=11 class=empty>No /relay:run runs yet. Run one on a medium ticket and they appear here.</td></tr>"
    base_rows = ""
    for s in sorted(base, key=lambda s: -s["start"])[:40]:
        base_rows += (f"<tr><td class=num>{day(s['start'])}</td><td>{project(s['cwd'])}</td><td class=num>{s['files']}</td>"
                      f"<td class=num>{s['work_ms'] / 60000:.1f}</td><td class=num>{fmt_min(per_file_min(s))}</td>"
                      f"<td class=num>{s['human_turns']}</td><td class=num>{fmt_tok(total_tokens(s))}</td></tr>")
    if not base_rows:
        base_rows = "<tr><td colspan=7 class=empty>No plain Opus sessions with code changes since tracking started.</td></tr>"
    recs = [s.get("record") for s in relay if s.get("record")]
    first = sum(1 for r in recs if r.get("verify") == "first")
    esc = sum(1 for s in relay if s["escalations"])
    facts = (f"{len(relay)} relay runs · {len(base)} plain Opus sessions · verification green on first try in "
             f"{first} of {len(recs)} recorded runs · escalated in {esc} of {len(relay)} runs")
    since = datetime.fromtimestamp(state.get("since", time.time())).strftime("%b %d, %Y")
    now = datetime.now().strftime("%b %d, %Y %H:%M")
    return TEMPLATE.format(head=e(head), facts=e(facts), cmp_rows=cmp_rows, run_rows=run_rows,
                           base_rows=base_rows, since=since, now=now, min_relay=MIN_RELAY, min_base=MIN_BASE)


TEMPLATE = """<title>Relay Scorecard</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;700&family=IBM+Plex+Sans:wght@400;500&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: one column, verdict first, then the comparison, then the run log. */
:root {{
  --bg: #f6f7f9; --surface: #ffffff; --fg: #18202b; --muted: #5d6878; --line: #dde2e9; --accent: #0b6e7a;
  --good: #1d7a46; --good-bg: #e3f3ea; --bad: #b3361f; --bad-bg: #fbe7e2; --neutral: #7a5a0b; --neutral-bg: #f8efd6;
  --muted-bg: #eceff3;
  --display: "Archivo", "Helvetica Neue", Arial, sans-serif;
  --body: "IBM Plex Sans", -apple-system, "Segoe UI", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, Menlo, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg: #11161d; --surface: #18202a; --fg: #e6ebf1; --muted: #98a3b3; --line: #2a3441; --accent: #4fc3cf;
  --good: #6fd39a; --good-bg: #173326; --bad: #f08a72; --bad-bg: #3a1d17; --neutral: #e3c26b; --neutral-bg: #352c14;
  --muted-bg: #222b36; color-scheme: dark; }} }}
:root[data-theme="dark"] {{
  --bg: #11161d; --surface: #18202a; --fg: #e6ebf1; --muted: #98a3b3; --line: #2a3441; --accent: #4fc3cf;
  --good: #6fd39a; --good-bg: #173326; --bad: #f08a72; --bad-bg: #3a1d17; --neutral: #e3c26b; --neutral-bg: #352c14;
  --muted-bg: #222b36; color-scheme: dark; }}
body {{ background: var(--bg); color: var(--fg); font: 15px/1.55 var(--body); }}
main {{ max-width: 1120px; margin: 0 auto; padding-block: 32px 56px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: grid; gap: 28px; }}
h1, h2 {{ font-family: var(--display); text-wrap: balance; margin: 0; }}
h1 {{ font-size: 1.9rem; letter-spacing: -0.01em; }}
h2 {{ font-size: 1.15rem; }}
.eyebrow {{ font: 500 0.75rem var(--mono); letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); }}
.meta {{ color: var(--muted); font-size: 0.875rem; }}
header {{ display: grid; gap: 6px; }}
.verdict {{ background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--accent);
  border-radius: 6px; padding: 18px 20px; display: grid; gap: 8px; }}
.verdict p {{ margin: 0; max-width: 75ch; }}
.verdict .big {{ font: 500 1.1rem/1.45 var(--display); }}
section {{ display: grid; gap: 10px; min-width: 0; }}
.scroll {{ overflow-x: auto; background: var(--surface); border: 1px solid var(--line); border-radius: 6px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.875rem; }}
th, td {{ text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }}
thead th {{ font: 500 0.72rem var(--mono); letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted);
  white-space: nowrap; }}
tbody tr:last-child > * {{ border-bottom: 0; }}
.num {{ font-family: var(--mono); font-variant-numeric: tabular-nums; white-space: nowrap; }}
.task {{ min-width: 180px; }}
.mix {{ font-family: var(--mono); font-size: 0.78rem; color: var(--muted); min-width: 160px; }}
.empty {{ color: var(--muted); text-align: center; padding: 22px; }}
.pill {{ display: inline-block; padding: 2px 9px; border-radius: 999px; font: 500 0.75rem var(--mono); white-space: nowrap; }}
.pill.good {{ color: var(--good); background: var(--good-bg); }}
.pill.bad {{ color: var(--bad); background: var(--bad-bg); }}
.pill.neutral {{ color: var(--neutral); background: var(--neutral-bg); }}
.pill.muted {{ color: var(--muted); background: var(--muted-bg); }}
.note {{ color: var(--muted); font-size: 0.85rem; max-width: 80ch; margin: 0; }}
.note li {{ margin-block: 4px; }}
</style>
<main>
  <header>
    <div class="eyebrow">/relay:run vs plain Opus</div>
    <h1>Relay Scorecard</h1>
    <div class="meta">Tracking since {since} · generated {now}</div>
  </header>

  <div class="verdict">
    <p class="big">{head}</p>
    <p class="meta">{facts}</p>
  </div>

  <section>
    <h2>Head to head, by task size</h2>
    <div class="scroll"><table>
      <thead><tr><th>Task size</th><th>Verdict</th><th>Runs (relay / opus)</th><th>Min per file</th><th>Working min</th><th>Your prompts</th><th>Tokens*</th></tr></thead>
      <tbody>{cmp_rows}</tbody>
    </table></div>
    <p class="note">Medians. Each cell shows relay / plain Opus. "Working min" is the time Claude was actually working (Claude Code's turn durations), not time spent waiting on you. A verdict needs {min_relay}+ relay runs and {min_base}+ Opus sessions in the same bucket, and a gap of 10% or more. *Tokens count input and output but not cache reads, which are cheap and would swamp the numbers.</p>
  </section>

  <section>
    <h2>Relay runs</h2>
    <div class="scroll"><table>
      <thead><tr><th>Started</th><th>Repo</th><th>Task</th><th>Files</th><th>Working min</th><th>Min / file</th><th>Your prompts</th><th>Agents</th><th>Escalations</th><th>Verify</th><th>Tokens by model</th></tr></thead>
      <tbody>{run_rows}</tbody>
    </table></div>
  </section>

  <section>
    <h2>Plain Opus baseline</h2>
    <div class="scroll"><table>
      <thead><tr><th>Started</th><th>Repo</th><th>Files</th><th>Working min</th><th>Min / file</th><th>Your prompts</th><th>Tokens</th></tr></thead>
      <tbody>{base_rows}</tbody>
    </table></div>
  </section>

  <section>
    <h2>How this is measured</h2>
    <ul class="note">
      <li>Source: your local Claude Code transcripts in ~/.claude/projects, including subagent transcripts. Nothing leaves the machine to build this page.</li>
      <li>A relay run lasts from a /relay:run call to the next one or the end of its session. The baseline is every other stretch of a session whose main model is Opus and that changed at least one file.</li>
      <li>Files changed counts distinct files edited by Claude or its subagents, excluding scratch and ~/.claude paths.</li>
      <li>Task difficulty is not controlled. Bucketing by files changed narrows that gap, but read a single bucket's verdict with its run count in mind.</li>
      <li>"Verify" comes from the orchestrator's own record at the end of each run: green on the first try, green after a fix, or failed.</li>
    </ul>
  </section>
</main>
"""


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    r = sub.add_parser("record")
    r.add_argument("--session", required=True)
    r.add_argument("--task", default="")
    r.add_argument("--slices", type=int, default=0)
    r.add_argument("--escalations", type=int, default=0)
    r.add_argument("--verify", choices=["first", "after-fix", "failed"], default=None)
    r.add_argument("--outcome", choices=["done", "partial", "abandoned"], default="done")
    r.add_argument("--files", type=int, default=0, help="files changed (git diff --stat); counts edits made outside Edit/Write")
    s = sub.add_parser("summary")
    s.add_argument("--session", default=None)
    rp = sub.add_parser("report")
    rp.add_argument("--quiet", action="store_true")
    sub.add_parser("nudge")
    u = sub.add_parser("set-url")
    u.add_argument("url")
    a = p.parse_args()
    if a.cmd == "scan":
        segs, _ = scan()
        print(f"{len(segs)} segments")
    else:
        {"record": cmd_record, "summary": cmd_summary, "report": cmd_report, "nudge": cmd_nudge, "set-url": cmd_set_url}[a.cmd](a)


if __name__ == "__main__":
    main()

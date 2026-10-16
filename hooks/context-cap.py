#!/usr/bin/env python3
"""PreToolUse hook: blocks a relay subagent's next tool call once its context passes the cap."""
import json, os, sys

CAPS = {
    "relay-scout": int(os.environ.get("RELAY_SCOUT_CAP", "60000")),
    "relay-builder": int(os.environ.get("RELAY_BUILDER_CAP", "120000")),
}

hook = json.load(sys.stdin)
agent = str(hook.get("agent_type", ""))
cap = next((v for k, v in CAPS.items() if k in agent), None)
if cap is None or not hook.get("agent_id"):
    sys.exit(0)

path = os.path.join(
    os.path.dirname(hook["transcript_path"]),
    hook["session_id"], "subagents", f"agent-{hook['agent_id']}.jsonl",
)
if not os.path.exists(path):
    sys.exit(0)

usage = {}
with open(path) as f:
    for line in f:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "assistant" and "usage" in d.get("message", {}):
            usage = d["message"]["usage"]

context = sum(usage.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
if context < cap:
    sys.exit(0)

print(
    f"CONTEXT CAP REACHED: your context is {context} tokens, cap is {cap}. "
    "Make no more tool calls. Reply now with your report, Status: HANDOFF, "
    "and a precise handoff of what is done and what remains.",
    file=sys.stderr,
)
sys.exit(2)

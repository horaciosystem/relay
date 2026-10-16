#!/usr/bin/env python3
"""PreToolUse hook: blocks a relay subagent's next tool call once its context passes the cap."""
import json, os, sys

CAPS = {
    "relay-scout": int(os.environ.get("RELAY_SCOUT_CAP", "60000")),
    "relay-builder": int(os.environ.get("RELAY_BUILDER_CAP", "120000")),
}
TAIL_BYTES = 512 * 1024

try:
    hook = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
agent = str(hook.get("agent_type") or "")
cap = next((v for k, v in CAPS.items() if k in agent), None)
agent_id = str(hook.get("agent_id") or "")
if cap is None or not agent_id:
    sys.exit(0)

path = hook.get("agent_transcript_path")
if not path and hook.get("transcript_path") and hook.get("session_id"):
    name = agent_id if agent_id.startswith("agent-") else f"agent-{agent_id}"
    path = os.path.join(os.path.dirname(hook["transcript_path"]), hook["session_id"], "subagents", f"{name}.jsonl")
if not path or not os.path.exists(path):
    sys.exit(0)

usage = {}
with open(path, "rb") as f:
    f.seek(max(0, os.path.getsize(path) - TAIL_BYTES))
    for line in f.read().splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        m = d.get("message") if isinstance(d, dict) else None
        if d.get("type") == "assistant" and isinstance(m, dict) and m.get("usage"):
            usage = m["usage"]

context = sum(usage.get(k, 0) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
if context < cap:
    sys.exit(0)

print(
    f"CONTEXT CAP REACHED: your context is {context} tokens, cap is {cap}. "
    "Make no more tool calls. Reply now with your report block: what is done, what is left.",
    file=sys.stderr,
)
sys.exit(2)

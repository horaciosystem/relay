---
description: Relay scorecard - is /relay:run faster than plain Opus? Builds the report and publishes it as an artifact.
allowed-tools: Bash(python3:*), Read, Artifact
---

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stats.py" report`. It prints JSON with `report` (an HTML file path), `artifact_url` (null on the first report), `headline`, `relay_runs` and `opus_sessions`.
2. Read the HTML file at `report`.
3. Publish it with the Artifact tool:
   - If `artifact_url` is set: `action: "read"` on that URL first, then publish `file_path` = the report path with `url` = `artifact_url`, so the link stays the same.
   - If it is null: publish `file_path` = the report path with `icon: "chart"` and a one-sentence description, then run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stats.py" set-url <the new artifact URL>`.
4. Reply with the link and 2-4 lines: the headline verdict in plain words, the biggest number behind it, and what would change it (for example "2 more medium runs before a verdict"). If relay is slower in a bucket, say so plainly and name the likely cause from the run table (escalations, many prompts from the user, slices that came back wrong or were handed off).

$ARGUMENTS

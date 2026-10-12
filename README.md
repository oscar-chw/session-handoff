# session-handoff

Experimental, local tools for a readable Claude Code → Codex import and an
explicit text handoff back to a **copy** of a Claude session. macOS/Linux,
Python 3.9+, standard library only. The optional active-session exporter needs
Node and Anthropic's Agent SDK.

This is a manual handoff utility, not a shared runtime or automatic two-way
session synchronizer. It does not proxy models, log into accounts, make model
requests, change either agent's global configuration, or publish session data.

## Why

Native imports can present historical tool calls as hundreds of assistant chat
messages. A long conversation can also exceed the model's context window even
when its full on-disk history is valid. A useful continuation needs both a
readable current conversation and access to the complete evidence behind it.

`bundle` preserves the full source transcript and the official SDK's active
conversation snapshot byte for byte. It creates a separate readable projection:
ordinary user messages retain their order, assistant prose is grouped by user
turn, historical metadata is labelled as context, and tool records remain in
the archive. Images/documents receive a visible archive placeholder. This is a
display projection, **not a claim that every original record appears in chat**.

`checkpoint` and `append` support repeated, completed Codex text turns back into
one Claude **copy**. Each turn is archived in full, each append has a pre-write
backup and recovery journal, repeated IDs do not duplicate content, and changed
destinations fail closed. Tool calls are preserved as data, not replayed as
executable Claude tools.

## Use on copies

Keep both apps from writing to the target session during a handoff. File locks
coordinate this utility's processes; they cannot stop an app that ignores the
lock. A last-moment comparison catches observed changes, but this is not an
atomic transaction with either app. Do not use it on an active conversation.

Generated bundles and checkpoints contain private history, possibly including
credentials that previously appeared in tool output. Keep them outside public
repositories. This repository contains only code and synthetic tests. Its
`.gitignore` is an aid, not a substitute for checking what you publish.

### 1. Export the resumable Claude conversation

Use a current **copy** of the source transcript and the official SDK's parent
chain, rather than treating every raw JSONL record as a current conversation.
The optional helper was exercised with `@anthropic-ai/claude-agent-sdk@0.3.296`:

```sh
npm install --no-save --ignore-scripts @anthropic-ai/claude-agent-sdk@0.3.296
node export-active.mjs SESSION_ID /path/to/project /private/history/active.json
```

It reads local session storage and refuses an existing output file. It does not
need a model reply. See the [official SDK](https://github.com/anthropics/claude-agent-sdk-typescript).

### 2. Build a private readable bundle

```sh
python3 handoff.py bundle \
  --source /private/history/claude-copy.jsonl \
  --active /private/history/active.json \
  --destination /private/history/readable-bundle \
  --cwd /path/to/project \
  --title 'Project continuation'
```

The destination must be new. `source.jsonl` retains all original records,
`active.json` retains the SDK snapshot, `conversation.jsonl` is the import
projection, and `manifest.json` records hashes and its new session UUID.

Use Codex's [native import](https://learn.chatgpt.com/docs/import) for the
projection. The tested native importer discovers sources inside Claude's
project-history directory; an arbitrary bundle path was rejected with
`session_not_detected`. Stage **only the projection** under its fresh UUID in
the matching Claude project-history directory, without replacing an existing
file, then select it in native import. This also creates a separate discoverable
Claude history entry; it is not your full original session. Keep the archive
and the full Claude copy for return handoffs.

This utility does not call the import API or modify Codex's private database.
The [app-server import API](https://learn.chatgpt.com/docs/app-server#detect-and-import-external-agent-config)
is an alternative for callers that already integrate with Codex.

### 3. Establish a return checkpoint

Determine the actual last resumable message UUID using the official Claude
reader. The last raw JSONL record can be metadata; do not guess its parent.

```sh
python3 handoff.py checkpoint \
  --target /private/history/full-claude-copy.jsonl \
  --directory /private/history/return-checkpoint \
  --leaf LAST_RESUMABLE_MESSAGE_UUID \
  --session-id COPIED_CLAUDE_SESSION_UUID \
  --cwd /path/to/project
```

The command checks record boundaries, that the supplied leaf exists, and the
session identity. It does **not** independently reconstruct Claude's active
branch; callers must supply the reader's actual leaf. The checkpoint directory
must be new. It never establishes a checkpoint by overwriting one.

### 4. Return a completed Codex turn

Save a completed turn object from Codex's `thread/read` response. A minimal
synthetic example is:

```json
{
  "id": "example-turn-1",
  "status": "completed",
  "items": [
    {"type": "userMessage", "content": [{"type": "text", "text": "Revise the draft."}]},
    {"type": "agentMessage", "text": "Here is the revised draft."}
  ]
}
```

```sh
python3 handoff.py append \
  --directory /private/history/return-checkpoint \
  --turn /private/history/completed-turn.json
```

Repeat the same command to check idempotence. Supply a new completed turn to
append another exchange. The assistant text retains its Codex provenance;
unsupported turn records remain in `turns/` with a visible archive note. The
whole target is hashed and backed up, so large sessions cost disk space and
time proportional to their size on each new handoff.

Prepared partial suffixes can be completed on retry. Unexpected bytes are
rejected, not truncated. Preserve the checkpoint directory alongside the copy.
Finally, read the copy back with Anthropic's SDK before opening it in Claude.

## Verification and limits

See [EVALUATION.md](EVALUATION.md) for the checks, observed results and limits.

```sh
python3 -m unittest discover -s tests -v
```

Tests use temporary directories and synthetic records. They cover source
preservation, Unicode, readable ordering, metadata attribution, nested tool
archiving, repeat handoffs, duplicate IDs, destination divergence, incomplete
turns, and interrupted-write recovery. No network or model calls run in tests.

Tested integration: a native Codex import on macOS using the official active
chain, a real Codex continuation, and exact offline readback of returned text
through Anthropic's SDK. A live authenticated Claude model continuation is
**not verified**. The original format-dependent pilot used Codex
`0.162.0-alpha.17.2`, Claude Code `2.1.224`, and SDK `0.3.296`.

Native formats and experimental app-server APIs can change. This does not
transfer account entitlements, provider-specific reasoning state, executable
tool behavior, or globally shared memory. It does not resolve simultaneous
edits, automatically carry future messages over, or prove a second live
Claude → Codex round trip. Windows is unsupported (`fcntl` is required).

MIT licensed. Not affiliated with Anthropic or OpenAI.

# Evaluation

Recorded 2026-10-13. This is evidence for a manual, copy-based text handoff;
it is not certification of general session compatibility.

## Reproducible checks

```sh
python3 -m unittest discover -s tests -v
ruff check --select E,F,B handoff.py tests
node --check export-active.mjs
gitleaks dir --redact --no-banner .
```

All 16 unit tests passed locally and on a second macOS machine using Python
3.9.6. Lint, JavaScript syntax validation, and the release-content secret scan
passed. The scanner is a supplemental check, not a guarantee about arbitrary
future session files.

| Property | Observed check |
|---|---|
| Readable history | Exact prose ordering, Unicode, assistant grouping, source metadata precedence, attachment placeholders |
| Preserved inputs | Byte-for-byte source and active-snapshot comparisons; existing destinations refused |
| Repeated handoffs | Two sequential synthetic turns keep parent links, exact text and the old transcript prefix |
| Idempotence | Same turn twice leaves identical bytes; reused ID with changed content is rejected |
| Divergence | Changed destination and unexpected partial bytes are rejected without rewriting them |
| Recovery | A prepared partial append completes on retry; a stale committed journal is cleared |
| Tool data | Unsupported records stay in the complete archived turn, without executable Claude tool calls |

Two deliberate mutations in temporary copies were rejected by the test suite:
dropping grouped assistant prose and writing the returned suffix at the start
of the destination. The real implementation and assertions were not changed
for these controls.

## Integration evidence

A private pilot used Codex `0.162.0-alpha.17.2`, Claude Code `2.1.224`, and
Anthropic Agent SDK `0.3.296` on macOS. It demonstrated:

- Native Codex import preserving the projected roles and text exactly.
- A real Codex reply that recovered the latest request from imported context
  and distinguished historical claims from freshly verified facts.
- Exact offline SDK readback of returned prompt and reply, with every earlier
  active message unchanged and parent links pointing to the prior active leaf.
- An identical retry producing no duplicate records.
- Unchanged hashes for the original session and its pre-existing supporting
  and registry files.

The private transcript and its receipts are deliberately absent from this
repository. These integration results cannot be reproduced with the public
synthetic fixtures alone. The public suite does not call either provider.

An independent code review found and prompted fixes for active-snapshot drift,
plain-string input handling, and metadata flag precedence; the corresponding
regression tests are included. Its final pass found no remaining actionable
blockers. This was a code review, not an independent execution of the checks.

## Unverified boundaries

No authenticated Claude model continuation or second live Claude-to-Codex
cycle was verified. The offline SDK result proves parsing and active-chain
recovery, not all model, tool, or UI behavior. Automatic synchronization,
concurrent app writes, Windows, and future native-format versions are outside
the verified scope. Keep an untouched original and use quiescent copies.

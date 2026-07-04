# Store Validated Zero state in TARGETS.md

Validated Zero state belongs beside the curated target definition in `TARGETS.md`, not only in Vikunja or transient SQLite state. It is human curation metadata about a source, so future maintainers need to review it with the URL, measures, notes, and administrator context that make the zero result credible.

## Consequences

Vikunja can hold the monthly Reverification Task, but the run should read the durable local fact from target metadata: who/when validated the zero state and when it is due for review again.

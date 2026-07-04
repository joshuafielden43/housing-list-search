# Use diff.csv for disappearance semantics

Staff-facing outputs must not infer closures independently from `run_prev.csv`. `diff.csv` is the source of truth for disappearance semantics because it has the DB-backed run state and the failed-authority context needed to distinguish `REMOVED`, `STALE`, and `SCRAPE_FAILED`.

## Consequences

`changelog_diffs.*` should become a projection of `diff.csv` for missing records: `REMOVED` only when the authority scrape succeeded, `SCRAPE_FAILED` when the authority failed, and `STALE` when the record is simply unconfirmed in the current run.

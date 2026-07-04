# Suspicious Zero does not fail the run

A Suspicious Zero should mark the affected authority as Needs Review, but it should not make a full run exit nonzero by itself. One city or adapter returning zero can represent only a handful of records out of hundreds of confirmed listings, so failing the whole run gives the anomaly too much weight.

## Consequences

Daily outputs should remain publishable when the rest of the run is healthy, but they must surface the affected authority clearly. Automation should create or update a Reverification Task for the Suspicious Zero instead of treating the whole run as failed.

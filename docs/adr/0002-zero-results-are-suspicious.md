# Treat zero-result inventory runs as suspicious

A zero-record result from a property-inventory source should not be treated as a clean closure signal by default. It is suspicious unless a person has already validated that the authority truly has no current public records for a dated review window.

## Consequences

Known empty sources need an explicit Validated Zero state. Those states should raise a Reverification Task monthly so the system does not silently preserve an old human judgment forever.

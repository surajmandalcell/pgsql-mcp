# Runtime observability

The server provides privacy-preserving runtime metrics.

## Recorded data

Metrics can include:

- tool call counts
- result outcomes
- call duration histograms
- active call counts
- truncation counts
- rollback counts
- correlation IDs

## Excluded data

Metrics do not store:

- SQL text
- parameter values
- database identifiers
- result values
- connection strings
- exception messages

## Endpoints

The optional HTTP exporter provides `/metrics` and `/healthz`.

It binds to loopback by default.

Remote binding requires explicit configuration.

## Cardinality

Tool labels use a bounded known set.

Unknown labels do not create unlimited metric series.

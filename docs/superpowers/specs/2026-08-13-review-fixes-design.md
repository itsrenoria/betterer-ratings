# Post-Merge Review Fixes Design

## Goal

Correct the behavioral, lint, and typing regressions identified in the changes after
`e20ec9b4a725f01e9e2bb95cba1ab04fc5cf8c18` without broadening the application architecture.

## PMDB ownership handling

PMDB IDs returned by ownership-blind duplicate or lookup resolution must not be persisted as
owned IDs. When a stale cached rating ID is detected, the handler will clear it and only persist
the replacement ID when the successful result represents a newly created rating. A duplicate
success may mark the row submitted, but its matched remote ID will not be cached. Mapping
preflight resolution follows the same rule: exact remote matches are submitted without storing
their remote IDs.

## Queue stall monitoring

The alert monitor will distinguish work that can progress now from retries scheduled for the
future. Queue status totals remain unchanged for the dashboard, while the progress loop supplies
separate due counts to the monitor. In-flight rows and due pending/retry rows count as active;
future-only retries do not start or sustain a stall alert.

## Static-analysis cleanup

The scoring calculation will narrow the optional aggregate value before passing it to
`clamp_0_100`, preserving runtime behavior while satisfying strict mypy. Ruff import-block
formatting errors will be corrected without unrelated formatting changes.

## Testing

Regression tests will cover:

- stale duplicate rating resolution not re-caching a foreign ID;
- mapping preflight not caching an ownership-unverified ID;
- future-only retries not producing stall alerts, while due work still does;
- the existing scoring behavior around a missing aggregate value.

Each behavioral test must fail before its production fix is applied. Final verification requires
the full pytest suite, Ruff, strict mypy, and `git diff --check` to pass.

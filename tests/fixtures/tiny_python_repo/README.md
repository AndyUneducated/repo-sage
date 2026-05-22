# tiny_python_repo

Hand-built fixture for the Phase 1 indexer end-to-end and graph-query
benchmark. Layout deliberately mimics a small web-app (auth + billing +
api + utils) so the resolver gets cross-module imports, class hierarchies,
self-method calls, and a sprinkle of unresolvable dynamic dispatch.

Files are tiny by design — the goal is to keep the benchmark expectations
auditable by humans, not to stress-test throughput. The 50 kLOC perf check
runs against a different repo (see `make bench-graph LARGE=1`).

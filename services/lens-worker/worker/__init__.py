"""BST Social Lens — Layer C spike worker (ADR-0004).

LOGGED-OUT ONLY. Fetches public permalinks from the seen frontier, extracts
what a logged-out visitor can see (OG metadata + embedded JSON), reports
fetched/failed/skipped back to lens-api, and produces a block-rate report
that decides go/no-go for promoting this spike to a service.
"""

WORKER_VERSION = "0.1.0"
PARSER_VERSION = f"worker-{WORKER_VERSION}"

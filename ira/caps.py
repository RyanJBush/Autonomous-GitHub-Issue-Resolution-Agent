"""Trusted hard caps. A request may lower any limit; it can never raise one. Documented in docs/POLICY.md."""

# Model response (raw bytes from the adapter, before JSON parsing).
MAX_RESPONSE_BYTES = 32_768

# Fields inside the response.
MAX_SUMMARY_CHARS = 500
MAX_ASSUMPTIONS = 5
MAX_ASSUMPTION_CHARS = 300
MAX_REASON_CHARS = 500

# Unified diff.
MAX_PATCH_BYTES = 8_192
MAX_HUNKS = 3
MAX_ADDED_LINES = 20
MAX_REMOVED_LINES = 20
MAX_CHANGED_LINES = 30  # added + removed
MAX_DIFF_LINE_BYTES = 300

# Operator-supplied inputs shown to the model.
MAX_ISSUE_TITLE_CHARS = 200
MAX_ISSUE_BODY_CHARS = 4_000
MAX_SOURCE_FILE_BYTES = 32_768

# Review packet: longest copy of model-derived text stored in packet.json / review.md.
MAX_PACKET_RAW_RESPONSE_BYTES = MAX_RESPONSE_BYTES
MAX_REVIEW_FIELD_CHARS = 600

# Subprocess (trusted git only) wall-clock limit, seconds.
GIT_TIMEOUT_S = 30

# Live model adapter (Milestone 3). A request may lower these; it can never raise them.
LIVE_MODELS = ("claude-sonnet-5-5",)
LIVE_MAX_TOKENS = 8_000
LIVE_TIMEOUT_S = 300
LIVE_COST_CAP_CENTS = 25
MAX_RECORDING_BYTES = 262_144

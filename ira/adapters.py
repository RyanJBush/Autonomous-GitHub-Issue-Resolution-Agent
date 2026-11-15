"""Model adapters. Milestone 1 ships only the synthetic fixture adapter; no live or replay adapter exists yet."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = PROJECT_ROOT / "fixtures"
SYNTHETIC_LABEL = "SYNTHETIC fixture output, hand-written; not produced by any model"


class AdapterError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def load_cases(fixtures_dir: Path = FIXTURES_DIR) -> list[dict]:
    data = json.loads((Path(fixtures_dir) / "cases.json").read_text(encoding="utf-8"))
    if data.get("label") != SYNTHETIC_LABEL:
        raise ValueError("fixture manifest is not labeled synthetic")
    return data["cases"]


@dataclass
class FixtureAdapter:
    """Returns the raw bytes of one recorded synthetic case. Ignores the prompt except for counting calls."""
    case_id: str
    fixtures_dir: Path = FIXTURES_DIR
    calls: int = 0

    def __post_init__(self):
        matches = [c for c in load_cases(self.fixtures_dir) if c["id"] == self.case_id]
        if len(matches) != 1:
            raise AdapterError("unknown_fixture_case", "fixture case id not found")
        self.case = matches[0]
        self._file = Path(self.fixtures_dir) / "responses" / self.case["file"] if self.case.get("file") else None
        self._sha = hashlib.sha256(self._file.read_bytes()).hexdigest() if self._file else None

    def identity(self) -> dict:
        return {"kind": "synthetic_fixture", "name": "FixtureAdapter", "label": SYNTHETIC_LABEL,
                "model": None, "fixture_case": self.case_id, "fixture_sha256": self._sha}

    def propose(self, prompt: str, max_bytes: int) -> bytes:
        self.calls += 1
        if self.calls > 1:
            raise AdapterError("attempt_limit", "only one proposal per run is allowed")
        if self.case.get("simulate") == "adapter_error":
            raise AdapterError("simulated_adapter_failure", "synthetic adapter failure (fixture)")
        with open(self._file, "rb") as handle:
            return handle.read(max_bytes + 1)  # one extra byte lets the parser detect oversize

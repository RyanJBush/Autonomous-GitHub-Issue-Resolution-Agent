"""Write (or --check) the runner and allowlist pins in demo/pinned.json.

Usage: python tools/pin_runner.py [--check]
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ira.imagebuild import inputs_sha256  # noqa: E402

FILES = ("runner/Dockerfile", "runner/requirements.lock", "runner/pytest.ini", "runner/run_tests.py", "runner/probe.py")
BASE_IMAGE = "python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b"


def sha(rel):
    return hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()


def main(argv):
    path = ROOT / "demo" / "pinned.json"
    pinned = json.loads(path.read_text())
    want = dict(pinned)
    want.pop("runner", None), want.pop("synthetic_allowlist", None), want.pop("reviewed_model_outputs", None)
    want["runner"] = {"files": {f: sha(f) for f in FILES}, "image_inputs_sha256": inputs_sha256(),
                      "base_image": BASE_IMAGE}
    want["synthetic_allowlist"] = {"path": "runner/synthetic_allowlist.json",
                                   "sha256": sha("runner/synthetic_allowlist.json")}
    want["reviewed_model_outputs"] = {"path": "runner/reviewed_model_outputs.json",
                                      "sha256": sha("runner/reviewed_model_outputs.json")}
    want["trusted_test"] = {**pinned["trusted_test"], "executed_in_m1": False}
    text = json.dumps(want, indent=2) + "\n"
    if "--check" in argv:
        return 0 if path.read_text() == text else 1
    path.write_text(text)
    print("pinned.json updated")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

import hashlib

import pytest

from conftest import BASE_BYTES
from ira import policy
from ira.models import Limits
from ira.policy import PolicyViolation, validate

P = "src/labels.py"
HDR = f"--- a/{P}\n+++ b/{P}\n"
GOOD = (HDR + "@@ -6,1 +6,1 @@\n-    return text.lower()\n+    return \" \".join(text.lower().split())\n")


def code(diff, limits):
    with pytest.raises(PolicyViolation) as info:
        validate(diff, P, BASE_BYTES, limits)
    return info.value.code


def test_good_patch_computes_exact_content(limits):
    vp = validate(GOOD, P, BASE_BYTES, limits)
    expected = BASE_BYTES.replace(b"    return text.lower()\n", b'    return " ".join(text.lower().split())\n')
    assert vp.new_content == expected
    assert (vp.added, vp.removed, len(vp.hunks)) == (1, 1, 1)
    assert vp.patch_sha256 == hashlib.sha256(GOOD.encode()).hexdigest()


def test_count_omitted_means_one(limits):
    assert validate(GOOD.replace("@@ -6,1 +6,1 @@", "@@ -6 +6 @@"), P, BASE_BYTES, limits).added == 1


def test_section_text_after_hunk_header_allowed(limits):
    validate(GOOD.replace("@@ -6,1 +6,1 @@", "@@ -6,1 +6,1 @@ def normalize_label(text: str) -> str:"),
             P, BASE_BYTES, limits)


def test_zero_count_insertion_and_deletion(limits):
    ins = HDR + "@@ -6,0 +7 @@\n+    # note\n"
    assert validate(ins, P, BASE_BYTES, limits).new_content.count(b"# note") == 1
    dele = HDR + "@@ -7,1 +6,0 @@\n-\n"
    assert validate(dele, P, BASE_BYTES, limits).removed == 1


def test_second_hunk_new_start_accounts_for_delta(limits):
    two = (HDR + "@@ -0,0 +1 @@\n+# header\n"
           "@@ -6 +7 @@\n-    return text.lower()\n+    return text.strip().lower()\n")
    assert validate(two, P, BASE_BYTES, limits).added == 2
    assert code(two.replace("@@ -6 +7 @@", "@@ -6 +6 @@"), limits) == "hunk_offset"


# The five patches that `git apply --check` accepted in the environment audit (Handoff 001), adapted to P.
GIT_APPLY_PROBE = {
    "rename": (f"diff --git a/{P} b/src/n.py\nsimilarity index 100%\nrename from {P}\nrename to src/n.py\n",
               "rename_not_allowed"),
    "mode": (f"diff --git a/{P} b/{P}\nold mode 100644\nnew mode 100755\n", "mode_change_not_allowed"),
    "symlink": ("diff --git a/link b/link\nnew file mode 120000\n--- /dev/null\n+++ b/link\n@@ -0,0 +1 @@\n"
                "+/etc/passwd\n\\ No newline at end of file\n", "symlink_not_allowed"),
    "offset": (GOOD.replace("@@ -6,1 +6,1 @@", "@@ -8,1 +8,1 @@"), "hunk_offset"),
}


@pytest.mark.parametrize("name", sorted(GIT_APPLY_PROBE))
def test_git_apply_probe_patches_rejected(name, limits):
    diff, expected = GIT_APPLY_PROBE[name]
    assert code(diff, limits) == expected


def test_git_apply_probe_valid_patch_accepted(limits):
    validate(GOOD, P, BASE_BYTES, limits)


@pytest.mark.parametrize("diff,expected", [
    (GOOD.replace("\n", "\r\n"), "crlf_not_allowed"),
    (GOOD.replace("split())", "split())\0"), "binary_not_allowed"),
    (GOOD.replace("split())", "split()) \x07"), "control_character"),
    (GOOD.replace("split())", "split()) \u2066"), "format_character"),
    (GOOD.replace("split())", "split()) \u2028"), "format_character"),
    (GOOD[:-1], "missing_final_newline"),
    (GOOD + "\\ No newline at end of file\n", "no_newline_marker_not_supported"),
    ("@@ -6,1 +6,1 @@\n-a\n+b\n", "missing_file_header"),
    (f"--- a/{P}\n", "missing_file_header"),
    (f"--- a/{P}\n--- b/{P}\n", "malformed_file_header"),
    (f"--- a/{P}\n+++ b/{P}\n", "no_hunks"),
    (f"garbage\n" + GOOD, "malformed_file_header"),
    (f"diff --git a/{P} b/src/util.py\n" + GOOD, "path_not_allowed"),
    (f"diff --git a/{P}\n" + GOOD, "malformed_file_header"),
    (GOOD.replace(f"--- a/{P}", f'--- "a/{P}"'), "quoted_path_not_allowed"),
    (GOOD.replace(f"--- a/{P}", f"--- {P}"), "malformed_file_header"),
    (GOOD.replace(f"+++ b/{P}", "+++ b/src/../src/labels.py"), "path_escape"),
    (GOOD.replace(f"+++ b/{P}", "+++ b/src//labels.py"), "path_escape"),
    (GOOD.replace(f"+++ b/{P}", "+++ b/src\\labels.py"), "path_escape"),
    (GOOD.replace(f"+++ b/{P}", "+++ b/C:/labels.py"), "path_escape"),
    (GOOD.replace(f"--- a/{P}", "--- a/src/labels.py "), "malformed_file_header"),
    (GOOD.replace(f"+++ b/{P}", "+++ b/conftest.py"), "test_path_not_allowed"),
    (GOOD.replace(f"+++ b/{P}", "+++ b/requirements.txt"), "config_path_not_allowed"),
    (GOOD.replace(f"+++ b/{P}", "+++ b/.github/x.py"), "workflow_path_not_allowed"),
    (GOOD.replace(f"+++ b/{P}", "+++ /dev/null"), "file_deletion_not_allowed"),
    (GOOD.replace(f"--- a/{P}", "--- /dev/null"), "file_creation_not_allowed"),
    (GOOD + f"diff --git a/src/util.py b/src/util.py\n", "multiple_files_not_allowed"),
    (GOOD + "+++ b/src/util.py\n", "multiple_files_not_allowed"),
    (GOOD + "index 0000000..1111111\n", "index_header_not_allowed"),
    (f"diff --git a/{P} b/{P}\ndeleted file mode 100644\n" + GOOD, "file_deletion_not_allowed"),
    (f"diff --git a/{P} b/{P}\nnew file mode 100644\n" + GOOD, "file_creation_not_allowed"),
    (f"diff --git a/{P} b/{P}\ndissimilarity index 90%\n" + GOOD, "rename_not_allowed"),
    (GOOD.replace("@@ -6,1 +6,1 @@", "@@ -6,1 +6,1@@"), "malformed_hunk_header"),
    (GOOD.replace("@@ -6,1 +6,1 @@", "@@ -6,a +6,1 @@"), "malformed_hunk_header"),
    (GOOD.replace("@@ -6,1 +6,1 @@", "@@ -9999999,1 +6,1 @@"), "malformed_hunk_header"),
    (GOOD.replace("@@ -6,1 +6,1 @@", "@@ -60,1 +60,1 @@"), "hunk_out_of_range"),
    (GOOD.replace("@@ -6,1 +6,1 @@", "@@ -0,1 +0,1 @@"), "hunk_out_of_range"),
    (GOOD.replace("@@ -6,1 +6,1 @@", "@@ -5,1 +5,1 @@"), "hunk_offset"),
    (GOOD.replace("@@ -6,1 +6,1 @@", "@@ -6,1 +7,1 @@"), "hunk_offset"),
    (GOOD.replace("-    return text.lower()", "-    return text.upper()"), "context_mismatch"),
    (GOOD.replace("+    return", "*    return"), "malformed_hunk_line"),
    (GOOD.replace("@@ -6,1 +6,1 @@", "@@ -6,2 +6,2 @@"), "truncated_hunk"),
    (GOOD.replace("@@ -6,1 +6,1 @@", "@@ -6,1 +6,0 @@"), "trailing_content"),
    (HDR + "@@ -6,1 +6,1 @@\n     return text.lower()\n", "no_op_hunk"),
    (HDR + "@@ -6,1 +6,1 @@\n-    return text.lower()\n+    return text.lower()\n", "no_change"),
])
def test_policy_rejections(diff, expected, limits):
    assert code(diff, limits) == expected


def test_overlap_and_order(limits):
    two = HDR + "@@ -6 +6 @@\n-    return text.lower()\n+    return 1\n@@ -6 +6 @@\n-    return text.lower()\n+    return 2\n"
    assert code(two, limits) == "hunk_order_or_overlap"
    rev = HDR + "@@ -6 +6 @@\n-    return text.lower()\n+    return 1\n@@ -1 +1 @@\n-x\n+y\n"
    assert code(rev, limits) == "hunk_order_or_overlap"


def test_hunk_offset_distinguished_from_mismatch(limits):
    # The old block exists in the base, just not at the stated line: reported as an offset, never applied.
    assert code(GOOD.replace("@@ -6,1 +6,1 @@", "@@ -9,1 +9,1 @@"), limits) == "hunk_offset"


def test_caps_exact_boundaries():
    def L(**kw):
        base = dict(max_response_bytes=32768, max_patch_bytes=8192, max_hunks=3, max_added_lines=20,
                    max_removed_lines=20, max_changed_lines=30)
        return Limits(**{**base, **kw})

    n = len(GOOD.encode())
    validate(GOOD, P, BASE_BYTES, L(max_patch_bytes=n))
    assert code(GOOD, L(max_patch_bytes=n - 1)) == "patch_too_large"
    validate(GOOD, P, BASE_BYTES, L(max_hunks=1))
    two = HDR + "@@ -0,0 +1 @@\n+# h\n@@ -6 +7 @@\n-    return text.lower()\n+    return 2\n"
    assert code(two, L(max_hunks=1)) == "too_many_hunks"
    assert code(GOOD, L(max_added_lines=1, max_removed_lines=1, max_changed_lines=1)) == "too_many_changed_lines"
    assert code(two, L(max_added_lines=1, max_changed_lines=1)) == "too_many_added_lines"
    validate(GOOD, P, BASE_BYTES, L(max_added_lines=1, max_removed_lines=1, max_changed_lines=2))


def test_removed_line_cap():
    lim = Limits(max_response_bytes=32768, max_patch_bytes=8192, max_hunks=3, max_added_lines=20,
                 max_removed_lines=1, max_changed_lines=21)
    diff = HDR + "@@ -6,2 +6,1 @@\n-    return text.lower()\n-\n+    return 1\n"
    assert code(diff, lim) == "too_many_removed_lines"


def test_line_length_cap(limits):
    long = GOOD.replace("split())", "split())  # " + "z" * (policy.caps.MAX_DIFF_LINE_BYTES))
    assert code(long, limits) == "line_too_long"


def test_policy_module_has_no_process_or_import_machinery():
    import ast
    tree = ast.parse(open(policy.__file__).read())
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in getattr(n, "names", [])} | {n.module.split(".")[0] for n in ast.walk(tree)
                                                     if isinstance(n, ast.ImportFrom) and n.module}
    assert not imported & {"subprocess", "os", "importlib", "runpy", "builtins", "ctypes"}
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not called & {"exec", "eval", "compile", "__import__", "open"}

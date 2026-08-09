"""Sabotage-score the rune-boundary truncation tests.

A suite that passes tells you nothing on its own. This breaks the fix in six
ways and checks that the tests notice five of them and, just as importantly,
do NOT notice the sixth. A scorer with no known-negative control reports
CAUGHT for everything and looks perfect while measuring nothing.

Two of these cases have to be written as drifted comparisons rather than
deletions: removing the walk-back orphans the utf8 import, and `go test` runs
vet, so the case reports a compile error instead of a score.

Run from anywhere:  python3 scripts/sabotage-truncation.py
"""

import os
import pathlib
import re
import signal
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Telling detection from the fixture falling over.
#
# `go test` exits non-zero when the suite catches the mutation and when a
# reach guard fires — the 33rd's `t.Fatalf` for "the input no longer reaches
# the code under test" — and this scorer read only that exit code. So a
# mutation that resizes the fixture out from under a guard scored CAUGHT with
# nothing having checked the mechanism the row names. Measured in multichat by
# the 42nd: a row reading CAUGHT while no assertion looked at the mechanism,
# and the catch would vanish the moment the fixture was resized, with the table
# still reading CAUGHT.
#
# GUARD_MARKERS names this suite's guards by message, because only this file
# knows which of its own t.Fatalf lines are guards rather than assertions.
GUARD_MARKERS = (
    'content was not truncated, so this test is not exercising the cut',
)

# A `go test` failure line: "    truncation_test.go:65: input no longer reaches".
# Anchored on the _test.go filename so a stack frame cannot be read as a message.
_FAIL_LINE = re.compile(r"^\s*(\S+_test\.go):(\d+): (.*)$", re.M)
# A stack frame naming a .go file. The absolute path is captured because the Go
# runtime's own frames (runtime/panic.go) would pass a bare-filename filter.
_FRAME = re.compile(r"^\s+(/\S+\.go):(\d+)", re.M)


def counts_as_coverage(verdict):
    """Whether a verdict means an assertion actually looked at the mechanism."""
    return verdict == "CAUGHT" or verdict.startswith("CAUGHT (panic in ")


def classify_caught(output):
    """Split a red run into detection and the fixture falling over.

    Evidence in descending order of strength. An assertion message is read
    before the stack, because a run can carry both — a suite that asserts the
    defect in one test and crashes on it in another is covered either way.
    """
    messages = [m for _, _, m in _FAIL_LINE.findall(output)]
    guard = [m for m in messages if any(g in m for g in GUARD_MARKERS)]
    real = [m for m in messages if m not in guard]
    if real:
        return "CAUGHT", real[0][:100]
    if "panic:" in output:
        # A mutation the test drove into a crash IS detection. A panic in the
        # fixture is the test falling over before asserting, which is not.
        frames = [f for f, _ in _FRAME.findall(output.split("panic:", 1)[1])
                  if f.startswith(str(REPO) + "/")]
        source = next((f for f in frames if not f.endswith("_test.go")), None)
        if source:
            return ("CAUGHT (panic in %s)" % pathlib.Path(source).name,
                    "the test drove the mutation into a crash")
        return "CAUGHT (fixture panicked)", "the test fell over before asserting"
    if guard:
        return "CAUGHT (guard)", guard[0][:100]
    return "CAUGHT (no message)", ""


def self_test():
    """Drive classify_caught() against every verdict it can return.

    The 43rd's rule: when you automate a check, the check is the next unmeasured
    claim, and no case table can score its own scorer. The 48th shipped a
    scorer whose unreachable branch read as a perfect run.
    """
    probes = [
        ("--- FAIL: T\n    x_test.go:9: values differ: got 1 want 2\n", "CAUGHT"),
        ("--- FAIL: T\n    x_test.go:9: content was not truncated, so this test is not exercising the cut\n", "CAUGHT (guard)"),
        ("--- FAIL: T\n    x_test.go:9: content was not truncated, so this test is not exercising the cut\n    x_test.go:12: got 1 want 2\n",
         "CAUGHT"),
        ("panic: boom\n\t/usr/lib/go/src/runtime/panic.go:8\n\t%s/util.go:3\n" % REPO,
         "CAUGHT (panic in util.go)"),
        ("panic: boom\n\t%s/x_test.go:3\n" % REPO, "CAUGHT (fixture panicked)"),
        ("--- FAIL: T\nno test line at all\n", "CAUGHT (no message)"),
    ]
    ok = True
    for output, want in probes:
        got, _ = classify_caught(output)
        if got != want:
            print("SELF-TEST FAIL: classify_caught -> %r, want %r" % (got, want))
            ok = False
    for verdict, want in [("CAUGHT", True), ("CAUGHT (panic in util.go)", True),
                          ("CAUGHT (guard)", False), ("CAUGHT (fixture panicked)", False),
                          ("CAUGHT (no message)", False), ("UNNOTICED", False)]:
        if counts_as_coverage(verdict) != want:
            print("SELF-TEST FAIL: counts_as_coverage(%r) != %r" % (verdict, want))
            ok = False
    print("self-test: %s" % ("all verdicts reachable and separated" if ok else "FAILED"))
    return 0 if ok else 1


if "--self-test" in sys.argv:
    sys.exit(self_test())

CASES = [
  # (label, file, old, new, expect_caught)
  ("the walk-back never runs, so the cut splits a rune again",
   "util.go", "for cut > 0 && !utf8.RuneStart(s[cut]) {", "for cut > len(s) && !utf8.RuneStart(s[cut]) {", True),
  ("the helper trims to nothing instead of to the boundary",
   "util.go", "return s[:cut]", "return s[:0]", True),
  ("the walk-back overshoots the boundary by one byte",
   "util.go", "\t}\n\treturn s[:cut]", "\t}\n\treturn s[:cut-1]", True),
  ("only the compaction call site reverts to a byte cut",
   "compaction.go", "truncateAtRuneBoundary(combined, 2000)", "combined[:2000]", True),
  ("only the preview call site reverts to a byte cut",
   "builder.go", "truncateAtRuneBoundary(content, previewChars)", "content[:previewChars]", True),
  # Known-NEGATIVE control. maxBytes==0 already returns "" via s[:0] on the
  # second branch, so narrowing the guard is a behavioural no-op. A harness that
  # reports CAUGHT for this is reporting CAUGHT for everything.
  ("CONTROL (no-op): the maxBytes guard narrows from <=0 to <0",
   "util.go", "if maxBytes <= 0 {", "if maxBytes < 0 {", False),
]

TESTS = "TestTruncateAtRuneBoundary|TestTruncateAtRuneBoundaryEdgeCases|TestCompactionContentStaysValidUTF8|TestTruncateMemoryToPreviewStaysValidUTF8"

def restore():
    subprocess.run(["git","checkout","--","util.go","compaction.go","builder.go"], cwd=REPO, check=True)

score = 0
# The file under test holds a deliberately broken version of itself from the write
# in the loop below until the next restore, and this script used to have no way out
# of that window except the ones it chooses to take. A killed run left the mutated
# file behind as ordinary-looking uncommitted work — a semantic edit to a tracked
# source file, which `git status` reports the same way it reports real work in
# progress, and which this box's standing rule tells the next agent not to throw
# away.
#
# A try/finally alone does NOT close this, and measuring it is how you find that
# out. Python raises KeyboardInterrupt for SIGINT, so a finally is on the way out
# for that one and for nothing else. SIGTERM and SIGHUP kill the process between
# the write and the restore — and those are exactly what a wall-clock cap, systemd
# and a process-group kill send. So the one signal a finally covers is the one you
# press by hand while watching, and the ones it misses are the ones an unattended
# run actually receives. Measured by kill on this scorer before these handlers
# existed: SIGTERM and SIGHUP each left the mutated file behind.
#
# The handler restores, reinstates the disposition it replaced and re-raises, so
# the process dies BY the signal (rc 128+signum). A handler that restores and
# exits 0 tells every caller a killed run succeeded.
#
# SIGKILL cannot be caught by the process that receives it. It is the one gap left
# here, and it is named rather than papered over.
_previous_handlers = {}


def _restore_and_reraise(signum, frame):
    restore()
    signal.signal(signum, _previous_handlers[signum])
    os.kill(os.getpid(), signum)


for _sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    _previous_handlers[_sig] = signal.signal(_sig, _restore_and_reraise)

try:
    for label, fname, old, new, expect in CASES:
        restore()
        p = REPO/fname
        text = p.read_text()
        if old not in text:
            print(f"  SETUP FAIL   {label}\n      pattern not found in {fname}"); continue
        p.write_text(text.replace(old, new, 1))
        r = subprocess.run(["go","test","-count=1","-run",TESTS,"."],
                           cwd=REPO, capture_output=True, text=True)
        out = r.stdout + r.stderr
        detail = ""
        if "build failed" in out or "cannot use" in out or "declared and not used" in out or "[build failed]" in out:
            verdict = "COMPILE ERROR"
        elif r.returncode != 0:
            verdict, detail = classify_caught(out)
        else:
            verdict = "UNNOTICED"
        # counts_as_coverage, not `verdict == "CAUGHT"`: a row that went red
        # because a fixture guard fired is not a mechanism this suite pins.
        ok = counts_as_coverage(verdict) == expect
        score += ok
        which = ""
        if verdict.startswith("CAUGHT"):
            which = " by: " + ",".join(sorted({l.split()[2].split("/")[0]
                     for l in out.splitlines() if l.startswith("--- FAIL:")}))
        if detail:
            which += "\n        \u21b3 " + detail
        print(f"  {'ok  ' if ok else 'BAD '} {verdict:<22} (want {'CAUGHT' if expect else 'UNNOTICED'}) {label}{which}")
finally:
    restore()
    for _sig, _handler in _previous_handlers.items():
        signal.signal(_sig, _handler)
print(f"\nscore {score}/{len(CASES)}")
sys.exit(0 if score == len(CASES) else 1)

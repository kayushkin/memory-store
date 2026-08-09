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
import signal
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

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
        if "build failed" in out or "cannot use" in out or "declared and not used" in out or "[build failed]" in out:
            verdict = "COMPILE ERROR"
        else:
            verdict = "CAUGHT" if r.returncode != 0 else "UNNOTICED"
        ok = (verdict == "CAUGHT") == expect
        score += ok
        which = ""
        if verdict == "CAUGHT":
            which = " by: " + ",".join(sorted({l.split()[2].split("/")[0]
                     for l in out.splitlines() if l.startswith("--- FAIL:")}))
        print(f"  {'ok  ' if ok else 'BAD '} {verdict:<13} (want {'CAUGHT' if expect else 'UNNOTICED'}) {label}{which}")
finally:
    restore()
    for _sig, _handler in _previous_handlers.items():
        signal.signal(_sig, _handler)
print(f"\nscore {score}/{len(CASES)}")
sys.exit(0 if score == len(CASES) else 1)

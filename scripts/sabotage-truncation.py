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

import pathlib
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

restore()
print(f"\nscore {score}/{len(CASES)}")
sys.exit(0 if score == len(CASES) else 1)

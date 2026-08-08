#!/usr/bin/env python3
"""Score the apostrophe tests in memory-store by injecting the defects they
exist for.

Verdict column, same inverted reading as the inber-party scorer:

    SOLE DETECTOR  only the new tests failed
    REDUNDANT      a pre-existing test also failed; the sibling is NAMED
    UNNOTICED      nothing failed
    VOID           the tree did not build, so no test ran

A REDUNDANT row must name its sibling. An unnamed one is indistinguishable
from the build-failure bug the forty-seventh pass hit, where two rows read
"a sibling covers this" and in fact no test had run at all.
"""

import subprocess
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER = "internal/textutil/capitalise.go"
REGISTRY = "tool_registry.go"

NEW_TESTS = {
    "TestToolRegistryHeadingKeepsAnApostropheInsideAWord",
    "TestToolRegistryHeadingStillBreaksOnAHyphen",
    "TestApostrophesDoNotBreakWords",
    "TestEverythingWithoutAnApostropheMatchesStringsTitle",
    "TestAnUnflankedApostropheStillSeparates",
    "TestInvalidUTF8IsCopiedNotReplaced",
    "TestWordStartIsTitleCaseNotUpperCase",
    "TestDifferentialAgainstStringsTitle",
}

MUTATIONS = [
    ("tool_registry heading reverts to strings.Title",
     REGISTRY,
     # Reverting the call site outright orphans the textutil import, which is a
     # compile error -- and unlike an orphaned variable, '&& false' cannot keep
     # an import live. Composing the two reintroduces the identical defect
     # ("agent's memory" -> "Agent'S Memory") with both symbols still referenced.
     "textutil.TitleFirstRuneOfEachWord(category)",
     "strings.Title(textutil.TitleFirstRuneOfEachWord(category))",
     "caught"),

    ("helper degraded to a first-rune-only capitaliser (the tempting simpler one)",
     HELPER,
     "\t\t\tatWordStart = !isWordRune(r)",
     "\t\t\tatWordStart = !isWordRune(r) && false",
     "caught"),

    ("apostrophe is a separator again (the original defect)",
     HELPER,
     "\t\tif r == '\\'' {",
     "\t\tif r == '\\'' && false {",
     "caught"),

    ("KNOWN POSITIVE: helper returns its input unchanged",
     HELPER,
     "\tvar b strings.Builder",
     "\tif true {\n\t\treturn s\n\t}\n\tvar b strings.Builder",
     "caught"),

    ("KNOWN NEGATIVE: drop the Grow pre-allocation (behaviour-neutral)",
     HELPER,
     "\tb.Grow(len(s))",
     "",
     "unnoticed"),
]


def run(cmd):
    return subprocess.run(cmd, cwd=REPO, shell=True, capture_output=True, text=True)


def restore():
    run("git checkout -- .")


def classify():
    build = run("go build ./... && go vet ./...")
    if build.returncode != 0:
        return "VOID", "did not build"
    test = run("go test -count=1 ./...")
    if test.returncode == 0:
        return "UNNOTICED", ""
    out = test.stdout + test.stderr
    if "[build failed]" in out:
        return "VOID", "test binary did not build"
    failing = set(re.findall(r"--- FAIL: (\w+)", out))
    if not failing:
        return "VOID", "non-zero exit with no FAIL line"
    siblings = failing - NEW_TESTS
    if siblings:
        return "REDUNDANT", "sibling: " + ", ".join(sorted(siblings))
    return "SOLE DETECTOR", ", ".join(sorted(failing))


def main():
    restore()
    if run("go test -count=1 ./...").returncode != 0:
        print("BASELINE NOT GREEN -- every row would be meaningless")
        return 1
    print("baseline green\n")

    rows = []
    for label, path, old, new, expectation in MUTATIONS:
        full = os.path.join(REPO, path)
        src = open(full).read()
        if src.count(old) != 1:
            restore()
            rows.append((label, "VOID", f"site appears {src.count(old)}x, not once", expectation))
            continue
        open(full, "w").write(src.replace(old, new, 1))
        verdict, detail = classify()
        restore()
        rows.append((label, verdict, detail, expectation))

    print(f"{'MUTATION':<72} | {'VERDICT':<14} | DETAIL")
    print("-" * 140)
    bad = 0
    for label, verdict, detail, expectation in rows:
        ok = (expectation == "caught" and verdict in ("SOLE DETECTOR", "REDUNDANT")) or (
            expectation == "unnoticed" and verdict == "UNNOTICED")
        bad += 0 if ok else 1
        print(f"{('* ' if not ok else '  ') + label:<72} | {verdict:<14} | {detail[:52]}")

    helper = open(os.path.join(REPO, HELPER)).read()
    print(f"\nfix still present after the run: {'unicode.ToTitle(r)' in helper}")
    print(f"tree clean after the run: {not run('git status --porcelain --untracked-files=no').stdout.strip()}")
    print(f"rows disagreeing with expectation: {bad}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

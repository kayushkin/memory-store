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

import os
import re
import signal
import subprocess
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
    # The mutated file holds a deliberately broken version of itself from the write
    # in the loop below until the restore that follows it, so every way out of that
    # window has to restore — including the ways this script does not choose to
    # take. A killed run left the mutated file behind as ordinary-looking
    # uncommitted work: a semantic edit to a tracked source file, which
    # `git status` reports the same way it reports real work in progress, and which
    # this box's standing rule tells the next agent not to throw away.
    #
    # A try/finally alone does NOT close this, and measuring it is how you find that
    # out. Python raises KeyboardInterrupt for SIGINT, so a finally is on the way
    # out for that one and for nothing else. SIGTERM and SIGHUP kill the process
    # between the write and the restore — and those are exactly what a wall-clock
    # cap, systemd and a process-group kill send. So the one signal a finally covers
    # is the one you press by hand while watching, and the ones it misses are the
    # ones an unattended run actually receives. Measured by kill on this scorer
    # before these handlers existed: SIGTERM left the mutated file behind.
    #
    # The handler restores, reinstates the disposition it replaced and re-raises, so
    # the process dies BY the signal (rc 128+signum). A handler that restores and
    # exits 0 tells every caller a killed run succeeded.
    #
    # SIGKILL cannot be caught by the process that receives it. It is the one gap
    # left here, and it is named rather than papered over.
    previous_handlers = {}

    def restore_and_reraise(signum, frame):
        restore()
        signal.signal(signum, previous_handlers[signum])
        os.kill(os.getpid(), signum)

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous_handlers[sig] = signal.signal(sig, restore_and_reraise)

    try:
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
    finally:
        restore()
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)

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

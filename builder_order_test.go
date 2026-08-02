package memorystore

import (
	"fmt"
	"path/filepath"
	"testing"

	_ "modernc.org/sqlite"
)

// storeWithTiedMemories saves n memories that score identically — same
// importance, same token count, no tags, so no tag bonus and the same recency
// bucket — under ids that sort ascending. insertOrder decides which id is
// written first, which is the only thing that differs between the two stores
// these tests build.
func storeWithTiedMemories(t *testing.T, name string, n int, insertOrder []int) *Store {
	t.Helper()

	s, err := NewStore(filepath.Join(t.TempDir(), name+".db"))
	if err != nil {
		t.Fatalf("create store: %v", err)
	}
	t.Cleanup(func() { s.Close() })

	for _, i := range insertOrder {
		m := Memory{
			ID:         fmt.Sprintf("tied-%03d", i),
			Content:    fmt.Sprintf("tied memory %03d", i),
			Importance: 0.4,
			Source:     "test",
			Tokens:     100,
		}
		if err := s.Save(m); err != nil {
			t.Fatalf("save %s: %v", m.ID, err)
		}
	}
	return s
}

func ascending(n int) []int {
	out := make([]int, n)
	for i := range out {
		out[i] = i
	}
	return out
}

func descending(n int) []int {
	out := make([]int, n)
	for i := range out {
		out[i] = n - 1 - i
	}
	return out
}

func idsOf(memories []Memory) []string {
	out := make([]string, len(memories))
	for i, m := range memories {
		out[i] = m.ID
	}
	return out
}

// TestBuildContextIgnoresTheOrderRowsArriveIn is the property the system prompt
// depends on and nothing asserted: the memories BuildContext returns, and the
// order it returns them in, are a function of the candidate SET, not of the
// order SQLite happened to hand the rows over in.
//
// It matters because the survivors become the whole `system` array of an
// Anthropic request and carry a cache breakpoint. Anthropic hashes
// tools -> system -> messages in order, so one memory swapping places with
// another invalidates that breakpoint and every breakpoint after it, and the
// conversation is re-charged at the 1.25x cache-write rate instead of the 0.10x
// read rate.
//
// The scan runs on idx_importance, so rows of equal importance arrive in rowid
// order — which is insertion order. Writing the same set of memories in the
// opposite order is therefore a faithful way to hand BuildContext the same
// candidates in a different arrival order, and it is not a hypothetical one:
// updateAccess multiplies importance by 1.01 on every read and DecayImportance
// multiplies it by 0.99 daily, so rows move within that index constantly.
func TestBuildContextIgnoresTheOrderRowsArriveIn(t *testing.T) {
	const n = 40

	forward := storeWithTiedMemories(t, "forward", n, ascending(n))
	backward := storeWithTiedMemories(t, "backward", n, descending(n))

	// A budget that admits only part of the tie group, so ordering decides
	// membership and not just position — the same cut inber makes at every one
	// of its 4000/6000/8000 budgets.
	req := BuildContextRequest{TokenBudget: 1000, IncludeAlwaysLoad: true}

	a, _, err := forward.BuildContext(req)
	if err != nil {
		t.Fatalf("BuildContext (forward): %v", err)
	}
	b, _, err := backward.BuildContext(req)
	if err != nil {
		t.Fatalf("BuildContext (backward): %v", err)
	}

	if len(a) == 0 || len(a) == n {
		t.Fatalf("budget did not cut inside the tie group: selected %d of %d", len(a), n)
	}

	gotA, gotB := idsOf(a), idsOf(b)
	if len(gotA) != len(gotB) {
		t.Fatalf("same candidates selected different counts: %d vs %d", len(gotA), len(gotB))
	}
	for i := range gotA {
		if gotA[i] != gotB[i] {
			t.Fatalf("arrival order changed the system prefix at position %d: %q vs %q\nforward:  %v\nbackward: %v",
				i, gotA[i], gotB[i], gotA, gotB)
		}
	}
}

// TestBuildContextTieOrderIsTotal pins the tie-break itself, so a later change
// that keeps determinism by accident — one that happens to work only because
// pdqsort leaves an all-equal run alone — does not read as coverage.
func TestBuildContextTieOrderIsTotal(t *testing.T) {
	const n = 8

	s := storeWithTiedMemories(t, "total", n, descending(n))

	got, _, err := s.BuildContext(BuildContextRequest{TokenBudget: 32000, IncludeAlwaysLoad: true})
	if err != nil {
		t.Fatalf("BuildContext: %v", err)
	}
	if len(got) != n {
		t.Fatalf("expected all %d memories inside the budget, got %d", n, len(got))
	}

	for i, m := range got {
		want := fmt.Sprintf("tied-%03d", i)
		if m.ID != want {
			t.Fatalf("position %d: want %s, got %s (full order %v)", i, want, m.ID, idsOf(got))
		}
	}
}

// TestSearchIgnoresTheOrderRowsArriveIn is the same property for Search, whose
// comment claims ties "keep the order the rows arrived in". That is only a
// contract if the arrival order is itself defined, and the query behind it has
// no ORDER BY.
func TestSearchIgnoresTheOrderRowsArriveIn(t *testing.T) {
	const n = 40

	forward := storeWithTiedMemories(t, "search-forward", n, ascending(n))
	backward := storeWithTiedMemories(t, "search-backward", n, descending(n))

	// Every tied memory has identical content-derived embedding weight relative
	// to a query that matches none of them, so similarity, importance and
	// recency are equal across the whole set.
	a, err := forward.Search("wholly unrelated query text", 10)
	if err != nil {
		t.Fatalf("Search (forward): %v", err)
	}
	b, err := backward.Search("wholly unrelated query text", 10)
	if err != nil {
		t.Fatalf("Search (backward): %v", err)
	}

	gotA, gotB := idsOf(a), idsOf(b)
	if len(gotA) != len(gotB) {
		t.Fatalf("same candidates returned different counts: %d vs %d", len(gotA), len(gotB))
	}
	if len(gotA) == 0 {
		t.Fatal("search returned nothing, so the ordering is not under test")
	}
	for i := range gotA {
		if gotA[i] != gotB[i] {
			t.Fatalf("arrival order changed the search result at position %d: %q vs %q\nforward:  %v\nbackward: %v",
				i, gotA[i], gotB[i], gotA, gotB)
		}
	}
}

package memorystore

import (
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// A repository's agents get one store each and run at the same time, so several
// connections write to one file while others read it. SQLite's default is to
// fail a contended statement immediately rather than wait, which turns ordinary
// concurrency into errors — and BuildContext's read is the one that assembles an
// agent's identity and instructions, so losing it costs a turn its whole system
// prompt.
func TestConcurrentStoresDoNotFailOnContention(t *testing.T) {
	path := filepath.Join(t.TempDir(), "memory.db")

	var wg sync.WaitGroup
	var mu sync.Mutex
	var writeFailures, readFailures []error

	for connection := 0; connection < 4; connection++ {
		wg.Add(1)
		go func(connection int) {
			defer wg.Done()
			store, err := NewStore(path)
			if err != nil {
				t.Errorf("connection %d: open: %v", connection, err)
				return
			}
			defer store.Close()

			for i := 0; i < 10; i++ {
				err := store.Save(Memory{
					ID:           string(rune('a'+connection)) + string(rune('0'+i%10)),
					Content:      "a memory worth keeping",
					Importance:   0.9,
					Source:       "test",
					LastAccessed: time.Now(),
					CreatedAt:    time.Now(),
				})
				if err != nil {
					mu.Lock()
					writeFailures = append(writeFailures, err)
					mu.Unlock()
				}

				if _, _, err := store.BuildContext(BuildContextRequest{TokenBudget: 4000}); err != nil {
					mu.Lock()
					readFailures = append(readFailures, err)
					mu.Unlock()
				}
			}
		}(connection)
	}
	wg.Wait()

	if len(readFailures) > 0 {
		t.Errorf("%d of 40 BuildContext reads failed under contention, first: %v", len(readFailures), readFailures[0])
	}
	if len(writeFailures) > 0 {
		t.Errorf("%d of 40 writes failed under contention, first: %v", len(writeFailures), writeFailures[0])
	}
}

// Complement, and the reason the test above is not just slow-and-lucky: both
// pragmas have to actually reach SQLite. modernc's driver drops a DSN key it
// does not recognise instead of rejecting it, so a misspelled one leaves the
// setting at its default and says nothing.
func TestStoreAppliesItsConcurrencyPragmas(t *testing.T) {
	store, err := NewStore(filepath.Join(t.TempDir(), "memory.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()

	var appliedTimeout int
	if err := store.db.QueryRow("PRAGMA busy_timeout").Scan(&appliedTimeout); err != nil {
		t.Fatalf("read back busy_timeout: %v", err)
	}
	if appliedTimeout != busyTimeout {
		t.Errorf("busy_timeout is %d, want %d — the DSN key did not reach SQLite", appliedTimeout, busyTimeout)
	}

	var appliedJournal string
	if err := store.db.QueryRow("PRAGMA journal_mode").Scan(&appliedJournal); err != nil {
		t.Fatalf("read back journal_mode: %v", err)
	}
	if !strings.EqualFold(appliedJournal, journalMode) {
		t.Errorf("journal_mode is %q, want %q — the DSN key did not reach SQLite", appliedJournal, journalMode)
	}
}

package memorystore

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"

	_ "modernc.org/sqlite"
)

// busyTimeout is how long a statement waits for another connection to release
// the database before giving up with SQLITE_BUSY.
const busyTimeout = 5000

// journalMode is what lets a reader and a writer hold the database at the same
// time. It is not a tuning knob here, it is the fix.
//
// One store per session and several sessions per repository means several
// connections use one file at once, and on the default rollback journal that is
// not concurrency, it is a queue: a reader blocks the writer's commit and a
// writer blocks every reader. SQLite's default is also not to wait at all, so a
// statement that arrives at a locked database fails on the spot. Both matter,
// and only together — measured on four concurrent stores doing forty writes and
// forty BuildContext reads:
//
//	default                     4 reads and 18 writes failed, in 0.07s
//	busy_timeout alone          8 writes still failed, and it took 50s
//	WAL + busy_timeout          nothing failed, in 0.12s
//
// The middle row is the trap: waiting alone does not break the queue, it only
// makes the failure slow. Readers never clear long enough for the writer to
// upgrade, so the timeout is spent rather than used.
//
// Reads are the ones that hurt — BuildContext's SELECT is what assembles an
// agent's identity and instructions, and a turn that loses it has no system
// prompt at all.
const journalMode = "WAL"

// NewStore creates or opens a memory store at the given path.
func NewStore(dbPath string) (*Store, error) {
	dir := filepath.Dir(dbPath)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return nil, fmt.Errorf("create db dir: %w", err)
	}

	// modernc's driver ignores a DSN key it does not recognise rather than
	// rejecting it, so `_pragma=` is the only spelling that reaches SQLite and a
	// typo here would leave both settings at their defaults without saying so.
	dsn := fmt.Sprintf("%s?_pragma=busy_timeout(%d)&_pragma=journal_mode(%s)", dbPath, busyTimeout, journalMode)
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("open db: %w", err)
	}

	// Create normalized schema
	// Note: We only create the table and basic indexes here
	// Migrations will add new columns to existing tables
	schema := `
	CREATE TABLE IF NOT EXISTS memories (
		id TEXT PRIMARY KEY,
		content TEXT NOT NULL,
		summary TEXT,
		original_id TEXT,
		importance REAL NOT NULL DEFAULT 0.5,
		access_count INTEGER NOT NULL DEFAULT 0,
		last_accessed INTEGER NOT NULL,
		created_at INTEGER NOT NULL,
		source TEXT NOT NULL,
		embedding BLOB
	);
	CREATE INDEX IF NOT EXISTS idx_importance ON memories(importance);
	CREATE INDEX IF NOT EXISTS idx_last_accessed ON memories(last_accessed);
	CREATE INDEX IF NOT EXISTS idx_created_at ON memories(created_at);
	CREATE INDEX IF NOT EXISTS idx_source ON memories(source);

	CREATE TABLE IF NOT EXISTS memory_tags (
		memory_id TEXT NOT NULL,
		tag TEXT NOT NULL,
		FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
	);
	CREATE INDEX IF NOT EXISTS idx_memory_tags_memory_id ON memory_tags(memory_id);
	CREATE INDEX IF NOT EXISTS idx_memory_tags_tag ON memory_tags(tag);

	`
	if _, err := db.Exec(schema); err != nil {
		db.Close()
		return nil, fmt.Errorf("create schema: %w", err)
	}

	// Create session-related tables
	if _, err := db.Exec(SessionsSchema()); err != nil {
		db.Close()
		return nil, fmt.Errorf("create sessions schema: %w", err)
	}

	// Run migrations for existing databases
	if err := runMigrations(db); err != nil {
		db.Close()
		return nil, fmt.Errorf("run migrations: %w", err)
	}

	return &Store{
		db:       db,
		embedder: NewEmbedder(),
	}, nil
}

// OpenOrCreate opens an existing memory store or creates a new one.
func OpenOrCreate(rootDir string) (MemoryStore, error) {
	localDBPath := DefaultMemoryPath(rootDir)
	if err := os.MkdirAll(filepath.Dir(localDBPath), 0755); err != nil {
		return nil, fmt.Errorf("create memory directory: %w", err)
	}

	return NewStore(localDBPath)
}

// NewSQLiteStore creates a new SQLite-backed memory store at the given path.
// This is the same as NewStore but with a more descriptive name.
func NewSQLiteStore(dbPath string) (MemoryStore, error) {
	return NewStore(dbPath)
}
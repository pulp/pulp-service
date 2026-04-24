package dedup

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestNewCache(t *testing.T) {
	tempDir := t.TempDir()
	cachePath := filepath.Join(tempDir, "cache.json")

	cache, err := NewCache(cachePath, 30*time.Minute)
	if err != nil {
		t.Fatalf("NewCache returned error: %v", err)
	}
	if cache == nil {
		t.Fatal("NewCache returned nil cache")
	}
}

func TestCacheSeen(t *testing.T) {
	tempDir := t.TempDir()
	cachePath := filepath.Join(tempDir, "cache.json")

	cache, err := NewCache(cachePath, 30*time.Minute)
	if err != nil {
		t.Fatalf("NewCache returned error: %v", err)
	}

	fingerprint := "abc123"
	startsAt := "2026-04-24T10:00:00Z"

	// Before recording, Seen should return false.
	if cache.Seen(fingerprint, startsAt) {
		t.Error("Seen returned true for unrecorded fingerprint")
	}

	// After recording, Seen should return true.
	cache.Record(fingerprint, startsAt)
	if !cache.Seen(fingerprint, startsAt) {
		t.Error("Seen returned false for recorded fingerprint")
	}

	// A different fingerprint should still return false.
	if cache.Seen("unknown", startsAt) {
		t.Error("Seen returned true for unknown fingerprint")
	}

	// Same fingerprint with different startsAt should return false.
	if cache.Seen(fingerprint, "2026-04-24T11:00:00Z") {
		t.Error("Seen returned true for same fingerprint with different startsAt")
	}
}

func TestCacheExpiry(t *testing.T) {
	tempDir := t.TempDir()
	cachePath := filepath.Join(tempDir, "cache.json")

	cache, err := NewCache(cachePath, 1*time.Millisecond)
	if err != nil {
		t.Fatalf("NewCache returned error: %v", err)
	}

	fingerprint := "expire-me"
	startsAt := "2026-04-24T10:00:00Z"

	cache.Record(fingerprint, startsAt)
	if !cache.Seen(fingerprint, startsAt) {
		t.Error("Seen returned false immediately after recording")
	}

	time.Sleep(5 * time.Millisecond)

	if cache.Seen(fingerprint, startsAt) {
		t.Error("Seen returned true after TTL expiry")
	}
}

func TestCachePersistence(t *testing.T) {
	tempDir := t.TempDir()
	cachePath := filepath.Join(tempDir, "cache.json")

	fingerprint := "persist-me"
	startsAt := "2026-04-24T10:00:00Z"

	// Create cache, record an entry, and save to disk.
	cache, err := NewCache(cachePath, 30*time.Minute)
	if err != nil {
		t.Fatalf("NewCache returned error: %v", err)
	}
	cache.Record(fingerprint, startsAt)
	if err := cache.Save(); err != nil {
		t.Fatalf("Save returned error: %v", err)
	}

	// Create a new cache from the same path; entry should survive.
	reloaded, err := NewCache(cachePath, 30*time.Minute)
	if err != nil {
		t.Fatalf("NewCache (reload) returned error: %v", err)
	}
	if !reloaded.Seen(fingerprint, startsAt) {
		t.Error("persisted entry not found after reload")
	}
}

func TestCacheCreationCaps(t *testing.T) {
	tempDir := t.TempDir()
	cachePath := filepath.Join(tempDir, "cache.json")

	cache, err := NewCache(cachePath, 30*time.Minute)
	if err != nil {
		t.Fatalf("NewCache returned error: %v", err)
	}

	// Record MaxPerRun (5) creations; each should be allowed.
	for iteration := range 5 {
		if !cache.CanCreateIssue() {
			t.Fatalf("CanCreateIssue returned false on iteration %d (should allow up to 5)", iteration)
		}
		cache.RecordIssueCreation()
	}

	// The 6th should be denied (per-run cap hit).
	if cache.CanCreateIssue() {
		t.Error("CanCreateIssue returned true after exceeding per-run cap")
	}
}

func TestFileLock(t *testing.T) {
	tempDir := t.TempDir()
	lockPath := filepath.Join(tempDir, "cache.lock")

	unlock, err := AcquireLock(lockPath)
	if err != nil {
		t.Fatalf("AcquireLock returned error: %v", err)
	}

	// Verify lock file exists on disk.
	if _, statErr := os.Stat(lockPath); os.IsNotExist(statErr) {
		t.Error("lock file does not exist after AcquireLock")
	}

	// Attempting to acquire the same lock should fail.
	_, secondErr := AcquireLock(lockPath)
	if secondErr == nil {
		t.Error("second AcquireLock should have returned an error")
	}

	// After unlocking, the lock file should be removed.
	unlock()
	if _, statErr := os.Stat(lockPath); !os.IsNotExist(statErr) {
		t.Error("lock file still exists after unlock")
	}

	// Acquiring again after unlock should succeed.
	unlockAgain, err := AcquireLock(lockPath)
	if err != nil {
		t.Fatalf("AcquireLock after unlock returned error: %v", err)
	}
	unlockAgain()
}

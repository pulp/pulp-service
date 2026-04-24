// Package dedup provides a fingerprint-based deduplication cache with TTL,
// Jira issue creation caps, and exclusive file locking.
package dedup

import (
	"encoding/json"
	"fmt"
	"os"
	"sync"
	"time"
)

// Creation caps — prevent runaway issue creation.
const (
	MaxPerRun  = 5
	MaxPerHour = 10
	MaxPerDay  = 20
)

// entry records when an alert fingerprint was last processed.
type entry struct {
	RecordedAt time.Time `json:"recorded_at"`
}

// cacheData is the on-disk JSON structure.
type cacheData struct {
	Entries           map[string]entry `json:"entries"`
	IssueCreationTimes []time.Time     `json:"issue_creation_times"`
}

// Cache is a thread-safe deduplication cache backed by a JSON file.
type Cache struct {
	mutex    sync.Mutex
	path     string
	cooldown time.Duration
	data     cacheData
	runCount int
}

// NewCache creates or loads a dedup cache from the given path.
// Expired entries are pruned on load.
func NewCache(path string, cooldown time.Duration) (*Cache, error) {
	cache := &Cache{
		path:     path,
		cooldown: cooldown,
		data: cacheData{
			Entries:           make(map[string]entry),
			IssueCreationTimes: nil,
		},
	}

	fileBytes, readErr := os.ReadFile(path)
	if readErr == nil {
		if jsonErr := json.Unmarshal(fileBytes, &cache.data); jsonErr != nil {
			return nil, fmt.Errorf("dedup: unmarshal cache %s: %w", path, jsonErr)
		}
		if cache.data.Entries == nil {
			cache.data.Entries = make(map[string]entry)
		}
	}

	cache.pruneExpired()
	return cache, nil
}

// cacheKey builds the lookup key from fingerprint + startsAt.
func cacheKey(fingerprint, startsAt string) string {
	return fingerprint + "|" + startsAt
}

// Seen returns true if the alert identified by fingerprint+startsAt
// has already been processed within the cooldown window.
func (cache *Cache) Seen(fingerprint, startsAt string) bool {
	cache.mutex.Lock()
	defer cache.mutex.Unlock()

	existing, found := cache.data.Entries[cacheKey(fingerprint, startsAt)]
	if !found {
		return false
	}
	return time.Since(existing.RecordedAt) < cache.cooldown
}

// Record marks an alert as processed right now.
func (cache *Cache) Record(fingerprint, startsAt string) {
	cache.mutex.Lock()
	defer cache.mutex.Unlock()

	cache.data.Entries[cacheKey(fingerprint, startsAt)] = entry{
		RecordedAt: time.Now(),
	}
}

// Save writes the cache to disk as JSON.
func (cache *Cache) Save() error {
	cache.mutex.Lock()
	defer cache.mutex.Unlock()

	jsonBytes, marshalErr := json.MarshalIndent(cache.data, "", "  ")
	if marshalErr != nil {
		return fmt.Errorf("dedup: marshal cache: %w", marshalErr)
	}
	if writeErr := os.WriteFile(cache.path, jsonBytes, 0644); writeErr != nil {
		return fmt.Errorf("dedup: write cache %s: %w", cache.path, writeErr)
	}
	return nil
}

// CanCreateIssue returns true if issue creation is allowed under all caps.
func (cache *Cache) CanCreateIssue() bool {
	cache.mutex.Lock()
	defer cache.mutex.Unlock()

	if cache.runCount >= MaxPerRun {
		return false
	}

	now := time.Now()
	hourAgo := now.Add(-1 * time.Hour)
	dayAgo := now.Add(-24 * time.Hour)

	hourlyCount := 0
	dailyCount := 0
	for _, creationTime := range cache.data.IssueCreationTimes {
		if creationTime.After(hourAgo) {
			hourlyCount++
		}
		if creationTime.After(dayAgo) {
			dailyCount++
		}
	}

	if hourlyCount >= MaxPerHour {
		return false
	}
	if dailyCount >= MaxPerDay {
		return false
	}
	return true
}

// RecordIssueCreation records that an issue was created right now.
func (cache *Cache) RecordIssueCreation() {
	cache.mutex.Lock()
	defer cache.mutex.Unlock()

	cache.runCount++
	cache.data.IssueCreationTimes = append(cache.data.IssueCreationTimes, time.Now())
}

// pruneExpired removes entries older than the cooldown window.
// Must be called with the mutex NOT held (called only from NewCache).
func (cache *Cache) pruneExpired() {
	for key, existing := range cache.data.Entries {
		if time.Since(existing.RecordedAt) >= cache.cooldown {
			delete(cache.data.Entries, key)
		}
	}
}

// AcquireLock creates an exclusive lock file at the given path.
// Returns an unlock function that removes the lock file.
// If the lock is already held, returns an error.
func AcquireLock(path string) (func(), error) {
	lockFile, openErr := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if openErr != nil {
		return nil, fmt.Errorf("dedup: acquire lock %s: %w", path, openErr)
	}
	lockFile.Close()

	unlock := func() {
		os.Remove(path)
	}
	return unlock, nil
}

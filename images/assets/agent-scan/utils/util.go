package utils

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"unicode/utf8"
)

// FileEntry holds the path and content of a single file extracted from an
// archive or read from disk.
type FileEntry struct {
	// Archive is the name of the source archive file (empty for plain files).
	Archive string
	// Path is the file path (inside the archive, or on disk).
	Path string
	// Content is the text content of the file, or a placeholder for binaries.
	Content string
	// IsBinary is true when the entry is a non‑text file.
	IsBinary bool
}

// ArchiveCache caches the extracted entries per archive path so that repeated
// tool calls (list_archive_files, read_archive_file) during a single scan do
// not re-extract the same archive.
type ArchiveCache struct {
	mu      sync.Mutex
	entries map[string][]FileEntry
}

// NewArchiveCache returns a ready-to-use cache.
func NewArchiveCache() *ArchiveCache {
	return &ArchiveCache{entries: make(map[string][]FileEntry)}
}

// Get returns the extracted entries for the given archive path, extracting and
// caching them on the first call.
func (c *ArchiveCache) Get(path string) ([]FileEntry, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	if cached, ok := c.entries[path]; ok {
		return cached, nil
	}

	result, err := ExtractFiles([]string{path})
	if err != nil {
		return nil, err
	}
	c.entries[path] = result
	return result, nil
}

// ExtractFiles reads the given paths and returns a slice of FileEntry values.
// For .tar.gz/.tgz paths every entry in the archive becomes its own FileEntry.
// For .whl paths the ZIP contents are extracted the same way.
// For plain files a single FileEntry is returned.
func ExtractFiles(paths []string) ([]FileEntry, error) {
	var entries []FileEntry
	for _, p := range paths {
		switch {
		case isTarGz(p):
			extracted, err := extractTarGz(p)
			if err != nil {
				return nil, fmt.Errorf("extract %s: %w", p, err)
			}
			entries = append(entries, extracted...)
		case isWhl(p):
			extracted, err := extractWhl(p)
			if err != nil {
				return nil, fmt.Errorf("extract %s: %w", p, err)
			}
			entries = append(entries, extracted...)
		default:
			data, err := os.ReadFile(p)
			if err != nil {
				return nil, fmt.Errorf("read %s: %w", p, err)
			}
			entries = append(entries, FileEntry{
				Path:    filepath.Base(p),
				Content: string(data),
			})
		}
	}
	return entries, nil
}

// isTarGz returns true if the file path has a .tar.gz or .tgz extension.
func isTarGz(path string) bool {
	lower := strings.ToLower(path)
	return strings.HasSuffix(lower, ".tar.gz") || strings.HasSuffix(lower, ".tgz")
}

// extractTarGz opens a .tar.gz archive and returns a FileEntry for every
// regular file inside it. Text files carry their full content; binary files
// carry a short placeholder instead.
func extractTarGz(archivePath string) ([]FileEntry, error) {
	f, err := os.Open(archivePath)
	if err != nil {
		return nil, fmt.Errorf("open archive: %w", err)
	}
	defer f.Close()

	gz, err := gzip.NewReader(f)
	if err != nil {
		return nil, fmt.Errorf("gzip reader: %w", err)
	}
	defer gz.Close()

	tr := tar.NewReader(gz)
	archiveName := filepath.Base(archivePath)

	var entries []FileEntry
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("reading tar entry: %w", err)
		}

		// Skip directories and non‑regular files.
		if hdr.Typeflag == tar.TypeDir || hdr.Typeflag != tar.TypeReg {
			continue
		}

		entry := FileEntry{
			Archive: archiveName,
			Path:    hdr.Name,
		}

		if isTextFile(hdr.Name) {
			data, err := io.ReadAll(io.LimitReader(tr, 1<<20)) // 1 MiB limit per file
			if err != nil {
				return nil, fmt.Errorf("reading %s: %w", hdr.Name, err)
			}
			entry.Content = string(data)
		} else {
			entry.IsBinary = true
			entry.Content = fmt.Sprintf("[binary file, %d bytes]", hdr.Size)
		}

		entries = append(entries, entry)
	}

	return entries, nil
}

// isWhl returns true if the file path has a .whl extension.
func isWhl(path string) bool {
	return strings.HasSuffix(strings.ToLower(path), ".whl")
}

// extractWhl opens a .whl (ZIP) archive and returns a FileEntry for every
// file inside it. Text files carry their full content; binary files carry a
// short placeholder instead.
func extractWhl(archivePath string) ([]FileEntry, error) {
	r, err := zip.OpenReader(archivePath)
	if err != nil {
		return nil, fmt.Errorf("open whl archive: %w", err)
	}
	defer r.Close()

	archiveName := filepath.Base(archivePath)

	var entries []FileEntry
	for _, f := range r.File {
		// Skip directories.
		if f.FileInfo().IsDir() {
			continue
		}

		entry := FileEntry{
			Archive: archiveName,
			Path:    f.Name,
		}

		if isTextFile(f.Name) {
			rc, err := f.Open()
			if err != nil {
				return nil, fmt.Errorf("open %s: %w", f.Name, err)
			}
			data, err := io.ReadAll(io.LimitReader(rc, 1<<20)) // 1 MiB limit per file
			rc.Close()
			if err != nil {
				return nil, fmt.Errorf("reading %s: %w", f.Name, err)
			}
			entry.Content = string(data)
		} else {
			entry.IsBinary = true
			entry.Content = fmt.Sprintf("[binary file, %d bytes]", f.UncompressedSize64)
		}

		entries = append(entries, entry)
	}

	return entries, nil
}

// isTextFile returns true if the file is likely a text file based on its extension.
func isTextFile(name string) bool {
	textExts := []string{
		".py", ".txt", ".md", ".rst", ".cfg", ".ini", ".toml", ".yaml", ".yml",
		".json", ".xml", ".html", ".htm", ".csv", ".in", ".spec", ".lock",
		".license", ".licence",
	}
	// Files with no extension that are commonly text in Python packages.
	base := strings.ToLower(filepath.Base(name))
	knownNames := []string{
		"readme", "license", "licence", "manifest.in", "pkg-info",
		"setup.py", "setup.cfg", "pyproject.toml", "makefile", "dockerfile",
		// .whl / .dist-info metadata files (no extension).
		"metadata", "record", "wheel", "top_level.txt", "entry_points.txt",
	}
	for _, known := range knownNames {
		if base == known {
			return true
		}
	}
	ext := strings.ToLower(filepath.Ext(name))
	for _, te := range textExts {
		if ext == te {
			return true
		}
	}
	return false
}

func Truncate(s string, max int) string {
	if len(s) <= max {
		return s
	}
	// Walk back to avoid splitting a multi-byte UTF-8 character.
	for max > 0 && !utf8.RuneStart(s[max]) {
		max--
	}
	return s[:max] + "\n... (truncated)"
}

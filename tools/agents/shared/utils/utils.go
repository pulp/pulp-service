package utils

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"
)

// ReadFiles reads the given file paths and returns their contents formatted
// as context blocks (filename + content).
func ReadFiles(paths []string) (string, error) {
	var sb strings.Builder
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			return "", fmt.Errorf("read %s: %w", path, err)
		}
		fmt.Fprintf(&sb, "=== File: %s ===\n%s\n\n", filepath.Base(path), string(data))
	}
	return sb.String(), nil
}

func Truncate(text string, max int) string {
	if len(text) <= max {
		return text
	}
	// Walk back to avoid splitting a multi-byte UTF-8 character.
	for max > 0 && !utf8.RuneStart(text[max]) {
		max--
	}
	return text[:max] + "\n... (truncated)"
}

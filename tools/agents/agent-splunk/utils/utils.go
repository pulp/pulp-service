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
	for _, p := range paths {
		data, err := os.ReadFile(p)
		if err != nil {
			return "", fmt.Errorf("read %s: %w", p, err)
		}
		fmt.Fprintf(&sb, "=== File: %s ===\n%s\n\n", filepath.Base(p), string(data))
	}
	return sb.String(), nil
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

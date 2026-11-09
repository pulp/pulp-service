package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRecordsToJSONFeedsHTMLReport(t *testing.T) {
	report := recordsToReport([]map[string]string{
		{"reqhost": "packages.redhat.com", "base_path": "api/pypi/public-rhai", "path_type": "PyPI", "_count": "1200"},
		{"reqhost": "console.redhat.com", "base_path": "api/pulp-content/foo", "path_type": "Content", "_count": "34"},
	})
	results, err := report.JSON()
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{`"reqHost": "packages.redhat.com"`, `"base_path": "api/pypi/public-rhai"`, `"count": "1200"`} {
		if !strings.Contains(results, want) {
			t.Errorf("missing %s in:\n%s", want, results)
		}
	}

	dir := t.TempDir()
	htmlPath, err := generateHTMLReport(dir, results)
	if err != nil {
		t.Fatal(err)
	}
	html, err := os.ReadFile(filepath.Clean(htmlPath))
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"packages.redhat.com", "api/pulp-content/foo", "1,234"} {
		if !strings.Contains(string(html), want) {
			t.Errorf("HTML report missing %q", want)
		}
	}
}

func TestPromptDataTruncatesButKeepsTotals(t *testing.T) {
	rows := make([]map[string]string, trafficPromptRows+5)
	for i := range rows {
		rows[i] = map[string]string{"reqhost": "packages.redhat.com", "base_path": fmt.Sprintf("api/pypi/p%d", i), "_count": "10"}
	}
	data, err := recordsToReport(rows).promptData()
	if err != nil {
		t.Fatal(err)
	}
	want := fmt.Sprintf("Total requests: %d across %d groups (top %d groups below)", 10*len(rows), len(rows), trafficPromptRows)
	if !strings.HasPrefix(data, want) {
		t.Errorf("unexpected header: %s", strings.SplitN(data, "\n", 2)[0])
	}
	if strings.Contains(data, fmt.Sprintf("p%d\"", trafficPromptRows)) {
		t.Error("rows beyond the prompt limit were included")
	}
}

package main

import (
	"fmt"
	"html/template"
	"os"
	"path/filepath"
	"regexp"
	"sort"
)

type reportEntry struct {
	Date string
}

func generateIndexPage(publicDir string) error {
	reportsDir := filepath.Join(publicDir, "reports")
	entries, err := os.ReadDir(reportsDir)
	if err != nil {
		return fmt.Errorf("read reports dir: %w", err)
	}

	datePattern := regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)
	var reports []reportEntry
	for _, entry := range entries {
		if entry.IsDir() && datePattern.MatchString(entry.Name()) {
			reports = append(reports, reportEntry{Date: entry.Name()})
		}
	}

	sort.Slice(reports, func(i, j int) bool {
		return reports[i].Date > reports[j].Date
	})

	indexPath := filepath.Join(publicDir, "index.html")
	f, err := os.Create(indexPath)
	if err != nil {
		return fmt.Errorf("create index file: %w", err)
	}
	defer f.Close()

	tmpl, err := template.New("index").Parse(indexTemplate)
	if err != nil {
		return fmt.Errorf("parse index template: %w", err)
	}

	return tmpl.Execute(f, reports)
}

const indexTemplate = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Akamai Traffic Reports</title>
<style>
  :root { --bg: #ffffff; --fg: #1a1a2e; --muted: #6b7280; --border: #e5e7eb; --card-bg: #f9fafb; --hover: #f3f4f6; --accent: #2563eb; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #0f172a; --fg: #e2e8f0; --muted: #94a3b8; --border: #334155; --card-bg: #1e293b; --hover: #1e293b; --accent: #60a5fa; }
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--fg); padding: 2rem; line-height: 1.5; max-width: 48rem; margin: 0 auto; }
  h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
  .subtitle { color: var(--muted); font-size: 0.875rem; margin-bottom: 2rem; }
  .report-list { list-style: none; }
  .report-list li { border: 1px solid var(--border); border-radius: 0.5rem; margin-bottom: 0.5rem; }
  .report-item { display: flex; align-items: center; justify-content: space-between; padding: 1rem 1.25rem; }
  .report-date { font-weight: 600; font-size: 1rem; }
  .report-links { display: flex; gap: 0.5rem; align-items: center; }
  .report-links a { padding: 0.25rem 0.75rem; font-size: 0.8125rem; border: 1px solid var(--border); border-radius: 0.375rem; text-decoration: none; color: var(--fg); transition: border-color 0.15s, color 0.15s; }
  .report-links a:hover { border-color: var(--accent); color: var(--accent); }
  .empty { color: var(--muted); font-style: italic; margin-top: 2rem; }
</style>
</head>
<body>
<h1>Akamai Traffic Reports</h1>
<p class="subtitle">7-day traffic analysis for hosted Pulp services</p>
{{if .}}
<ul class="report-list">
{{range .}}  <li>
    <div class="report-item">
      <span class="report-date">{{.Date}}</span>
      <div class="report-links">
        <a href="reports/{{.Date}}/traffic.html">Report</a>
        <a href="reports/{{.Date}}/traffic.json">JSON</a>
      </div>
    </div>
  </li>
{{end}}</ul>
{{else}}
<p class="empty">No reports available yet.</p>
{{end}}
</body>
</html>`

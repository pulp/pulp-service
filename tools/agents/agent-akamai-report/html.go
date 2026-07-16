package main

import (
	"encoding/json"
	"fmt"
	"html/template"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"time"
)

type trafficRow struct {
	ReqHost  string
	BasePath string
	PathType string
	Count    int
}

type htmlReportData struct {
	GeneratedAt string
	TotalCount  int
	Rows        []trafficRow
}

func generateHTMLReport(outputDir, resultsJSON string) (string, error) {
	var parsed struct {
		Results []map[string]any `json:"results"`
	}
	if err := json.Unmarshal([]byte(resultsJSON), &parsed); err != nil {
		return "", fmt.Errorf("parse results JSON: %w", err)
	}

	rows := make([]trafficRow, 0, len(parsed.Results))
	totalCount := 0
	for _, r := range parsed.Results {
		count := 0
		if v, ok := r["count"]; ok {
			switch c := v.(type) {
			case string:
				count, _ = strconv.Atoi(c)
			case float64:
				count = int(c)
			}
		}
		totalCount += count
		rows = append(rows, trafficRow{
			ReqHost:  strVal(r, "reqHost"),
			BasePath: strVal(r, "base_path"),
			PathType: strVal(r, "path_type"),
			Count:    count,
		})
	}

	sort.Slice(rows, func(i, j int) bool { return rows[i].Count > rows[j].Count })

	data := htmlReportData{
		GeneratedAt: time.Now().UTC().Format("2006-01-02 15:04:05 UTC"),
		TotalCount:  totalCount,
		Rows:        rows,
	}

	outPath := filepath.Join(outputDir, "traffic.html")
	f, err := os.Create(outPath)
	if err != nil {
		return "", fmt.Errorf("create HTML file: %w", err)
	}
	defer f.Close()

	tmpl, err := template.New("report").Funcs(template.FuncMap{
		"commaInt": func(n int) string {
			s := strconv.Itoa(n)
			if len(s) <= 3 {
				return s
			}
			var result []byte
			for i, c := range s {
				if i > 0 && (len(s)-i)%3 == 0 {
					result = append(result, ',')
				}
				result = append(result, byte(c))
			}
			return string(result)
		},
	}).Parse(htmlTemplate)
	if err != nil {
		return "", fmt.Errorf("parse HTML template: %w", err)
	}

	if err := tmpl.Execute(f, data); err != nil {
		return "", fmt.Errorf("execute HTML template: %w", err)
	}

	return outPath, nil
}

func strVal(m map[string]any, key string) string {
	if v, ok := m[key]; ok {
		if s, ok := v.(string); ok {
			return s
		}
	}
	return ""
}

const htmlTemplate = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Akamai Traffic Report</title>
<style>
  :root { --bg: #ffffff; --fg: #1a1a2e; --muted: #6b7280; --border: #e5e7eb; --header-bg: #f9fafb; --row-hover: #f3f4f6; --accent: #2563eb; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #0f172a; --fg: #e2e8f0; --muted: #94a3b8; --border: #334155; --header-bg: #1e293b; --row-hover: #1e293b; --accent: #60a5fa; }
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--fg); padding: 2rem; line-height: 1.5; }
  h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
  .meta { color: var(--muted); font-size: 0.875rem; margin-bottom: 1.5rem; }
  .summary { display: flex; gap: 2rem; margin-bottom: 1.5rem; }
  .summary .stat { background: var(--header-bg); border: 1px solid var(--border); border-radius: 0.5rem; padding: 1rem 1.5rem; }
  .summary .stat-value { font-size: 1.5rem; font-weight: 700; }
  .summary .stat-label { font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
  table { width: 100%; border-collapse: collapse; font-size: 0.875rem; }
  thead th { position: sticky; top: 0; background: var(--header-bg); border-bottom: 2px solid var(--border); padding: 0.75rem 1rem; text-align: left; font-weight: 600; cursor: pointer; user-select: none; white-space: nowrap; }
  thead th:hover { color: var(--accent); }
  thead th .sort-indicator { margin-left: 0.25rem; font-size: 0.75rem; }
  tbody td { padding: 0.5rem 1rem; border-bottom: 1px solid var(--border); }
  tbody tr:hover { background: var(--row-hover); }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .type-badge { display: inline-block; padding: 0.125rem 0.5rem; border-radius: 9999px; font-size: 0.75rem; font-weight: 500; }
  .type-Content { background: #dbeafe; color: #1e40af; }
  .type-Container { background: #dcfce7; color: #166534; }
  .type-API { background: #fef3c7; color: #92400e; }
  .type-PyPI { background: #ede9fe; color: #5b21b6; }
  .type-Lightwell { background: #fce7f3; color: #9d174d; }
  .type-Other { background: var(--header-bg); color: var(--muted); }
  @media (prefers-color-scheme: dark) {
    .type-Content { background: #1e3a5f; color: #93c5fd; }
    .type-Container { background: #14532d; color: #86efac; }
    .type-API { background: #78350f; color: #fde68a; }
    .type-PyPI { background: #3b0764; color: #c4b5fd; }
    .type-Lightwell { background: #831843; color: #f9a8d4; }
    .type-Other { background: var(--header-bg); color: var(--muted); }
  }
  #filter { padding: 0.5rem 0.75rem; border: 1px solid var(--border); border-radius: 0.375rem; background: var(--bg); color: var(--fg); font-size: 0.875rem; width: 20rem; max-width: 100%; margin-bottom: 1rem; }
</style>
</head>
<body>
<h1>Akamai Traffic Report</h1>
<p class="meta">Generated {{.GeneratedAt}} &middot; Last 7 days</p>

<div class="summary">
  <div class="stat">
    <div class="stat-value">{{commaInt .TotalCount}}</div>
    <div class="stat-label">Total Requests</div>
  </div>
  <div class="stat">
    <div class="stat-value">{{len .Rows}}</div>
    <div class="stat-label">Unique Paths</div>
  </div>
</div>

<input type="text" id="filter" placeholder="Filter by host, path, or type...">

<table id="traffic-table">
<thead>
  <tr>
    <th data-col="0">Host <span class="sort-indicator"></span></th>
    <th data-col="1">Base Path <span class="sort-indicator"></span></th>
    <th data-col="2">Type <span class="sort-indicator"></span></th>
    <th data-col="3">Count <span class="sort-indicator">&#9660;</span></th>
  </tr>
</thead>
<tbody>
{{range .Rows}}  <tr>
    <td>{{.ReqHost}}</td>
    <td><code>{{.BasePath}}</code></td>
    <td><span class="type-badge type-{{.PathType}}">{{.PathType}}</span></td>
    <td class="num">{{commaInt .Count}}</td>
  </tr>
{{end}}</tbody>
</table>

<script>
(function() {
  const table = document.getElementById("traffic-table");
  const tbody = table.tBodies[0];
  const headers = table.querySelectorAll("thead th");
  let sortCol = 3, sortAsc = false;

  headers.forEach((th, i) => {
    th.addEventListener("click", () => {
      if (sortCol === i) { sortAsc = !sortAsc; } else { sortCol = i; sortAsc = i !== 3; }
      sortTable();
      updateIndicators();
    });
  });

  function sortTable() {
    const rows = Array.from(tbody.rows);
    rows.sort((a, b) => {
      let va = a.cells[sortCol].textContent.trim();
      let vb = b.cells[sortCol].textContent.trim();
      if (sortCol === 3) { va = parseInt(va.replace(/,/g, ""), 10); vb = parseInt(vb.replace(/,/g, ""), 10); return sortAsc ? va - vb : vb - va; }
      return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
    });
    rows.forEach(r => tbody.appendChild(r));
  }

  function updateIndicators() {
    headers.forEach((th, i) => {
      const span = th.querySelector(".sort-indicator");
      span.textContent = i === sortCol ? (sortAsc ? "\u25B2" : "\u25BC") : "";
    });
  }

  document.getElementById("filter").addEventListener("input", function() {
    const q = this.value.toLowerCase();
    Array.from(tbody.rows).forEach(r => {
      r.style.display = r.textContent.toLowerCase().includes(q) ? "" : "none";
    });
  });
})();
</script>
</body>
</html>`

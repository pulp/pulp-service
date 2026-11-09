package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"time"

	"github.com/pulp/pulp-service/tools/agents/shared/sumo"
)

// trafficQuery aggregates 7 days of Akamai DataStream access logs (Sumo Logic
// _index=rh_akamai) by host, base path (first three path segments) and path type.
// Sumo field names are lowercase and reqpath has no leading slash. The keyword
// scope ("packages" OR "console") only narrows the scan; the where clauses do
// the actual filtering.
const trafficQuery = `_index=rh_akamai ("packages" OR "console")
| json auto
| where reqhost matches "*packages*redhat.com" or reqhost matches "*console*redhat.com"
| where reqpath matches "api/pulp*" or reqpath matches "api/pypi*" or reqpath matches "v2*" or reqpath matches "extensions/v2*" or reqpath matches "trusted-libraries*" or reqpath matches "lightwell*" or reqpath matches "pulp/container*"
| parse regex field=reqpath "^(?<base_path>[^/]*(?:/[^/]*){0,2})"
| if(reqpath matches "api/pulp-content*", "Content",
    if(reqpath matches "api/pulp-container*", "Container",
    if(reqpath matches "api/pulp*", "API",
    if(reqpath matches "api/pypi*", "PyPI",
    if(reqpath matches "v2*" or reqpath matches "extensions/v2*" or reqpath matches "pulp/container*", "Container",
    if(reqpath matches "trusted-libraries*", "PyPI",
    if(reqpath matches "lightwell*", "Lightwell", "Other"))))))) as path_type
| count by reqhost, base_path, path_type
| sort by _count desc`

const (
	trafficWindow     = 7 * 24 * time.Hour
	trafficMaxRows    = 50000 // all groups, so the HTML totals are complete
	trafficPromptRows = 100   // top groups passed to the LLM
	trafficJobTimeout = 15 * time.Minute
)

// trafficRecord is one row of traffic.json. The keys predate the move from
// Splunk to Sumo Logic and are kept so published reports stay comparable.
type trafficRecord struct {
	ReqHost  string `json:"reqHost"`
	BasePath string `json:"base_path"`
	PathType string `json:"path_type"`
	Count    string `json:"count"`
}

// trafficReport is the content of traffic.json.
type trafficReport struct {
	Results []trafficRecord `json:"results"`
	// Warnings returned by Sumo Logic for the search (e.g. partial results).
	Warnings []string `json:"warnings,omitempty"`
}

// queryTraffic runs trafficQuery over the last 7 days.
func queryTraffic(ctx context.Context, client *sumo.Client) (trafficReport, error) {
	client.SetJobTimeout(trafficJobTimeout)
	to := time.Now().UTC()
	res, err := client.SearchRows(ctx, trafficQuery, to.Add(-trafficWindow), to, trafficMaxRows)
	if err != nil {
		return trafficReport{}, err
	}
	if res.Kind != "records" && res.Total > 0 {
		return trafficReport{}, fmt.Errorf("traffic query returned %s instead of aggregate records", res.Kind)
	}
	fmt.Fprintf(os.Stderr, "[sumo] traffic query returned %d of %d records\n", len(res.Rows), res.Total)
	report := recordsToReport(res.Rows)
	report.Warnings = res.Warnings
	if len(res.Rows) < res.Total {
		report.Warnings = append(report.Warnings,
			fmt.Sprintf("only %d of %d groups were fetched; totals are incomplete", len(res.Rows), res.Total))
	}
	for _, w := range report.Warnings {
		fmt.Fprintf(os.Stderr, "[sumo] warning: %s\n", w)
	}
	return report, nil
}

// recordsToReport converts Sumo aggregate records (lowercase fields, _count)
// to the traffic.json format.
func recordsToReport(rows []map[string]string) trafficReport {
	report := trafficReport{Results: make([]trafficRecord, 0, len(rows))}
	for _, r := range rows {
		report.Results = append(report.Results, trafficRecord{
			ReqHost:  r["reqhost"],
			BasePath: r["base_path"],
			PathType: r["path_type"],
			Count:    r["_count"],
		})
	}
	return report
}

// JSON renders the report as indented JSON.
func (r trafficReport) JSON() (string, error) {
	b, err := json.MarshalIndent(r, "", "  ")
	if err != nil {
		return "", err
	}
	return string(b), nil
}

// promptData returns the data passed to the LLM: the top trafficPromptRows
// groups (results are sorted by count) plus the overall totals.
func (r trafficReport) promptData() (string, error) {
	total := 0
	for _, row := range r.Results {
		n, _ := strconv.Atoi(row.Count)
		total += n
	}
	top := r
	if len(top.Results) > trafficPromptRows {
		top.Results = top.Results[:trafficPromptRows]
	}
	data, err := top.JSON()
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("Total requests: %d across %d groups (top %d groups below).\n\n%s",
		total, len(r.Results), len(top.Results), data), nil
}

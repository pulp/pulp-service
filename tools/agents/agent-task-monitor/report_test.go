package main

import (
	"encoding/json"
	"regexp"
	"strings"
	"testing"
	"time"
)

func TestFormatResource(t *testing.T) {
	names := map[string]string{"prn:rpm.rpmrepository:0199aaaa-bbbb": "my-repo"}
	cases := map[string]string{
		"prn:rpm.rpmrepository:0199aaaa-bbbb": "RPM Repo: my-repo",
		"prn:core.domain:0123456789abcdef":    "Domain: 01234567",
		"prn:other.type:0123456789abcdef":     "other.type: 01234567",
		"pdrn:0123456789abcdef:distributions": "Distributions: 01234567",
		"plain-resource":                      "plain-resource",
	}
	for prn, want := range cases {
		if got := formatResource(prn, names); got != want {
			t.Errorf("formatResource(%q) = %q, want %q", prn, got, want)
		}
	}
}

func TestBuildIncidents(t *testing.T) {
	now := mustTime("2026-08-25T12:00:00Z")
	ev := func(ts, name string) Event {
		return Event{Timestamp: mustTime(ts), EventType: eventUnexplainedWait, TaskName: name, TaskHref: "/" + name + "/"}
	}
	events := []Event{
		ev("2026-08-25T10:00:00Z", "a"),
		ev("2026-08-25T10:05:00Z", "b"),
		ev("2026-08-25T10:10:00Z", "a"),
		{Timestamp: mustTime("2026-08-25T11:00:00Z"), EventType: eventWorkerSaturation},
		ev("2026-08-25T11:55:00Z", "c"),
	}
	got := buildIncidents(events, now)
	if len(got) != 2 {
		t.Fatalf("incidents = %+v", got)
	}
	if !got[0].Ongoing || got[0].Count != 1 || got[0].Tasks[0].Name != "c" {
		t.Errorf("newest = %+v", got[0])
	}
	if got[1].Ongoing || got[1].Count != 3 || len(got[1].Tasks) != 2 || !got[1].End.Equal(mustTime("2026-08-25T10:10:00Z")) {
		t.Errorf("oldest = %+v", got[1])
	}
}

func TestThroughputProrates(t *testing.T) {
	ref := mustTime("2026-08-25T12:00:00Z")
	snaps := []Snapshot{
		{WindowStart: ref.Add(-10 * time.Minute), WindowEnd: ref.Add(-5 * time.Minute), CompletedCount: 10},
		{WindowStart: ref.Add(-5 * time.Minute), WindowEnd: ref, CompletedCount: 4, FailedCount: 2},
	}
	c, f, _ := throughputForWindow(snaps, 5*time.Minute, ref)
	if c != 4 || f != 2 {
		t.Errorf("5min = %d/%d", c, f)
	}
	c, _, _ = throughputForWindow(snaps, 7*time.Minute+30*time.Second, ref)
	if c != 9 { // 4 + half of 10
		t.Errorf("7.5min = %d", c)
	}
}

func TestRenderHTML(t *testing.T) {
	now := mustTime("2026-08-25T12:00:00Z")
	st := newState("pulp-tasks-prod", "https://packages.redhat.com")
	st.Snapshots = []Snapshot{{
		WindowStart: now.Add(-5 * time.Minute), WindowEnd: now, Live: true,
		CompletedCount: 3, WaitingCount: 1, RunningCount: 2, OnlineWorkers: 4, WorkerSaturation: 0.5,
		ResourceQueues: []ResourceQueue{{Resource: "prn:core.domain:abc", Waiting: 1, Status: "anomaly"}},
	}}
	d := newDailySummary("2026-08-25")
	d.Completed, d.DurationSum, d.DurationCount = 3, 6, 3
	d.TaskTypes["</script><script>alert(1)</script>"] = &TaskTypeStats{Count: 3, DurationSum: 6, DurationCount: 3}
	d.Resources["prn:core.domain:abc"] = &ResourceCounts{Exclusive: 3}
	st.Daily[d.Date] = d
	st.Events = []Event{{Timestamp: now.Add(-time.Minute), EventType: eventUnexplainedWait, Severity: severityWarning, TaskName: "t"}}

	page := BuildPage(st, map[string]string{"prn:core.domain:abc": "my-domain"}, now)
	if page.ActiveProblems != 1 || page.Latest == nil || page.Latest.ResourceQueues[0].Resource != "Domain: my-domain" {
		t.Errorf("page = %+v", page)
	}
	if len(page.DaySummaries) != historyDays || page.DaySummaries[0].Date != "2026-08-25" || page.DaySummaries[0].MaxSeverity != severityWarning {
		t.Errorf("days = %+v", page.DaySummaries[0])
	}
	if page.DayDetails["2026-08-25"].AvgDuration != "2.0s" || page.DayDetails["2026-08-24"].AvgDuration != "N/A" {
		t.Errorf("details = %+v", page.DayDetails["2026-08-25"])
	}
	if len(page.MonthlySummaries) != 1 || page.MonthlySummaries[0].Month != "2026-08" || page.MonthlySummaries[0].Total != 3 {
		t.Errorf("monthly = %+v", page.MonthlySummaries)
	}
	if page.Saturation.Avg1h == nil || *page.Saturation.Avg1h != 0.5 {
		t.Errorf("saturation = %+v", page.Saturation)
	}

	out, err := RenderHTML(page)
	if err != nil {
		t.Fatal(err)
	}
	html := string(out)
	if strings.Contains(html, "__") && regexp.MustCompile(`__[A-Z_]+__`).MatchString(html) {
		t.Error("unreplaced placeholder")
	}
	if strings.Contains(html, "</script><script>alert") {
		t.Error("task name broke out of the script tag")
	}
	if !strings.Contains(html, "<title>packages.redhat.com Status (pulp-tasks-prod)</title>") {
		t.Error("missing title")
	}
	m := regexp.MustCompile(`(?m)^const DATA = (.*);$`).FindStringSubmatch(html)
	if m == nil {
		t.Fatal("DATA not found")
	}
	var decoded map[string]any
	if err := json.Unmarshal([]byte(m[1]), &decoded); err != nil {
		t.Fatalf("DATA is not valid JSON: %v", err)
	}
	for _, key := range []string{"latest", "throughput", "saturation", "daySummaries", "dayDetails", "incidents", "monthlySummaries", "activeProblems", "generatedAt"} {
		if _, ok := decoded[key]; !ok {
			t.Errorf("DATA missing %s", key)
		}
	}
}

func TestRenderEmptyState(t *testing.T) {
	page := BuildPage(newState("e", "https://x"), nil, time.Now())
	out, err := RenderHTML(page)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(out), `"latest":null`) || !strings.Contains(string(out), `"incidents":[]`) {
		t.Error("empty state should render null latest and empty incidents")
	}
}

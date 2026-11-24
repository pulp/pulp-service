package main

import (
	"context"
	_ "embed"
	"encoding/json"
	"fmt"
	"html"
	"math"
	"regexp"
	"sort"
	"strings"
	"time"
)

//go:embed page.html.tmpl
var pageTemplate string

const (
	// incidentGap joins unexplained_wait events into one incident; an incident
	// is ongoing while its last detection is within this gap of now.
	incidentGap     = 10 * time.Minute
	resourceNameTTL = 24 * time.Hour
	historyDays     = 30
)

var timeWindows = []struct {
	Label string
	Span  time.Duration
}{
	{"5 min", 5 * time.Minute},
	{"1 hour", time.Hour},
	{"12 hours", 12 * time.Hour},
	{"24 hours", 24 * time.Hour},
	{"7 days", 7 * 24 * time.Hour},
	{"30 days", 30 * 24 * time.Hour},
}

var resourceTypeLabels = map[string]string{
	"core.domain":                   "Domain",
	"rpm.rpmrepository":             "RPM Repo",
	"python.pythonrepository":       "Python Repo",
	"file.filerepository":           "File Repo",
	"container.containerrepository": "Container Repo",
	"ansible.ansiblerepository":     "Ansible Repo",
}

var pdrnDomainRe = regexp.MustCompile(`^pdrn:([^:]+):`)

// formatResource renders a PRN as "<type>: <name>", using the resolved name
// when there is one and a shortened id otherwise.
func formatResource(prn string, names map[string]string) string {
	if !strings.HasPrefix(prn, "prn:") && !strings.HasPrefix(prn, "pdrn:") {
		return prn
	}
	label, short := "Resource", truncateRunes(prn, 12)
	if m := prnRe.FindStringSubmatch(prn); m != nil {
		label = resourceTypeLabels[m[1]]
		if label == "" {
			label = m[1]
		}
		short = truncateRunes(m[2], 8)
	} else if m := pdrnDomainRe.FindStringSubmatch(prn); m != nil {
		label, short = "Distributions", truncateRunes(m[1], 8)
	}
	if name := names[prn]; name != "" {
		short = name
	}
	return label + ": " + short
}

func truncateRunes(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}

// Incident groups unexplained_wait events no more than incidentGap apart.
type Incident struct {
	Start   time.Time      `json:"start"`
	End     time.Time      `json:"end"`
	Count   int            `json:"count"`
	Ongoing bool           `json:"ongoing"`
	Tasks   []incidentTask `json:"tasks"`
}

type incidentTask struct {
	Name string `json:"name"`
	Href string `json:"href"`
}

// buildIncidents returns incidents newest first.
func buildIncidents(events []Event, now time.Time) []Incident {
	var ue []Event
	for _, e := range events {
		if e.EventType == eventUnexplainedWait {
			ue = append(ue, e)
		}
	}
	sort.SliceStable(ue, func(i, j int) bool { return ue[i].Timestamp.Before(ue[j].Timestamp) })

	var incidents []Incident
	for _, ev := range ue {
		if n := len(incidents); n > 0 && ev.Timestamp.Sub(incidents[n-1].End) <= incidentGap {
			last := &incidents[n-1]
			last.End = ev.Timestamp
			last.Count++
			if ev.TaskName != "" && !hasTaskName(last.Tasks, ev.TaskName) {
				last.Tasks = append(last.Tasks, incidentTask{ev.TaskName, ev.TaskHref})
			}
			continue
		}
		inc := Incident{Start: ev.Timestamp, End: ev.Timestamp, Count: 1, Tasks: []incidentTask{}}
		if ev.TaskName != "" {
			inc.Tasks = append(inc.Tasks, incidentTask{ev.TaskName, ev.TaskHref})
		}
		incidents = append(incidents, inc)
	}
	for i := range incidents {
		incidents[i].Ongoing = now.Sub(incidents[i].End) <= incidentGap
	}
	for i, j := 0, len(incidents)-1; i < j; i, j = i+1, j-1 {
		incidents[i], incidents[j] = incidents[j], incidents[i]
	}
	if incidents == nil {
		incidents = []Incident{}
	}
	return incidents
}

func hasTaskName(tasks []incidentTask, name string) bool {
	for _, t := range tasks {
		if t.Name == name {
			return true
		}
	}
	return false
}

// --- Page data (shape consumed by the JavaScript in page.html.tmpl) ---

type throughputRow struct {
	Label     string `json:"label"`
	Completed int    `json:"completed"`
	Failed    int    `json:"failed"`
	Canceled  int    `json:"canceled"`
}

type daySummary struct {
	Date        string   `json:"date"`
	Completed   int      `json:"completed"`
	Failed      int      `json:"failed"`
	Canceled    int      `json:"canceled"`
	MaxSeverity string   `json:"maxSeverity"`
	EventCount  int      `json:"eventCount"`
	EventTypes  []string `json:"eventTypes"`
}

type taskTypeRow struct {
	Name        string `json:"name"`
	Count       int    `json:"count"`
	AvgDuration string `json:"avgDuration"`
}

type dayEvent struct {
	Timestamp time.Time `json:"timestamp"`
	EventType string    `json:"event_type"`
	Severity  string    `json:"severity"`
	TaskName  string    `json:"task_name"`
}

type dayDetail struct {
	Completed    int           `json:"completed"`
	Failed       int           `json:"failed"`
	Canceled     int           `json:"canceled"`
	AvgDuration  string        `json:"avgDuration"`
	TaskTypes    []taskTypeRow `json:"taskTypes"`
	TopResources []resourceRow `json:"topResources"`
	Events       []dayEvent    `json:"events"`
}

type monthlyTaskType struct {
	Name        string   `json:"name"`
	Count       int      `json:"count"`
	AvgDuration *float64 `json:"avgDuration"`
}

type monthlySummary struct {
	Month                string            `json:"month"`
	Completed            int               `json:"completed"`
	Failed               int               `json:"failed"`
	Canceled             int               `json:"canceled"`
	Total                int               `json:"total"`
	AvgDuration          *float64          `json:"avgDuration"`
	TaskTypes            []monthlyTaskType `json:"taskTypes"`
	EventCount           int               `json:"eventCount"`
	PeakWorkerSaturation float64           `json:"peakWorkerSaturation"`
	AvgWorkerSaturation  float64           `json:"avgWorkerSaturation"`
	SnapshotCount        int               `json:"snapshotCount"`
}

type saturationSummary struct {
	Avg1h  *float64 `json:"avg1h"`
	Peak1h *float64 `json:"peak1h"`
}

type pageData struct {
	ModelName        string               `json:"modelName"`
	BaseURL          string               `json:"baseUrl"`
	GeneratedAt      string               `json:"generatedAt"`
	Latest           *Snapshot            `json:"latest"`
	ActiveProblems   int                  `json:"activeProblems"`
	Throughput       []throughputRow      `json:"throughput"`
	Saturation       saturationSummary    `json:"saturation"`
	DaySummaries     []daySummary         `json:"daySummaries"`
	DayDetails       map[string]dayDetail `json:"dayDetails"`
	Incidents        []Incident           `json:"incidents"`
	MonthlySummaries []monthlySummary     `json:"monthlySummaries"`
}

func latestLiveSnapshot(st *State) *Snapshot {
	var latest *Snapshot
	for i := range st.Snapshots {
		s := &st.Snapshots[i]
		if s.Live && (latest == nil || s.WindowStart.After(latest.WindowStart)) {
			latest = s
		}
	}
	return latest
}

func latestSnapshotEnd(st *State, now time.Time) time.Time {
	var end time.Time
	var latestStart time.Time
	for _, s := range st.Snapshots {
		if end.IsZero() || s.WindowStart.After(latestStart) {
			latestStart, end = s.WindowStart, s.WindowEnd
		}
	}
	if end.IsZero() {
		return now
	}
	return end
}

// throughputForWindow prorates each snapshot's counts by its overlap with
// [ref-span, ref).
func throughputForWindow(snaps []Snapshot, span time.Duration, ref time.Time) (completed, failed, canceled int) {
	cutoff := ref.Add(-span)
	for _, s := range snaps {
		if !s.WindowEnd.After(cutoff) || !s.WindowStart.Before(ref) {
			continue
		}
		dur := s.WindowEnd.Sub(s.WindowStart)
		if dur <= 0 {
			continue
		}
		overlapStart, overlapEnd := s.WindowStart, s.WindowEnd
		if cutoff.After(overlapStart) {
			overlapStart = cutoff
		}
		if ref.Before(overlapEnd) {
			overlapEnd = ref
		}
		ratio := float64(overlapEnd.Sub(overlapStart)) / float64(dur)
		completed += int(math.Round(float64(s.CompletedCount) * ratio))
		failed += int(math.Round(float64(s.FailedCount) * ratio))
		canceled += int(math.Round(float64(s.CanceledCount) * ratio))
	}
	return completed, failed, canceled
}

// avgTaskDuration is the count-weighted mean duration for a day, or 3s when
// unknown (the default the swamp monitor used for saturation estimates).
func avgTaskDuration(d *DailySummary) float64 {
	if d == nil || d.DurationCount == 0 {
		return 3.0
	}
	return d.DurationSum / float64(d.DurationCount)
}

// saturation1h averages saturation over the last hour of snapshots. Buckets
// without a live poll estimate it from throughput and average task duration.
func saturation1h(st *State, latest *Snapshot, ref time.Time) saturationSummary {
	if latest == nil || latest.OnlineWorkers <= 0 {
		return saturationSummary{}
	}
	workers := float64(latest.OnlineWorkers)
	var values []float64
	for _, s := range st.Snapshots {
		if !s.WindowEnd.After(ref.Add(-time.Hour)) {
			continue
		}
		windowSec := s.WindowEnd.Sub(s.WindowStart).Seconds()
		switch {
		case windowSec <= 0 || s.WorkerSaturation > 0:
			values = append(values, s.WorkerSaturation)
		case s.CompletedCount+s.FailedCount+s.CanceledCount == 0:
			values = append(values, 0)
		default:
			total := float64(s.CompletedCount + s.FailedCount + s.CanceledCount)
			est := total * avgTaskDuration(st.Daily[dateKey(s.WindowStart)]) / (windowSec * workers)
			values = append(values, math.Min(1, est))
		}
	}
	if len(values) == 0 {
		return saturationSummary{}
	}
	sum, peak := 0.0, values[0]
	for _, v := range values {
		sum += v
		peak = math.Max(peak, v)
	}
	avg := sum / float64(len(values))
	return saturationSummary{Avg1h: &avg, Peak1h: &peak}
}

func formatSeconds(sum float64, count int) string {
	if count == 0 {
		return "—"
	}
	return fmt.Sprintf("%.1fs", sum/float64(count))
}

var severityOrder = map[string]int{"none": 0, severityInfo: 1, severityWarning: 2, severityCritical: 3}

func buildDays(st *State, names map[string]string, now time.Time) ([]daySummary, map[string]dayDetail) {
	today := now.UTC().Truncate(24 * time.Hour)
	summaries := make([]daySummary, 0, historyDays)
	details := map[string]dayDetail{}
	index := map[string]int{}
	for i := range historyDays {
		key := dateKey(today.AddDate(0, 0, -i))
		sum := daySummary{Date: key, MaxSeverity: "none", EventTypes: []string{}}
		det := dayDetail{AvgDuration: "N/A", TaskTypes: []taskTypeRow{}, TopResources: []resourceRow{}, Events: []dayEvent{}}
		if d := st.Daily[key]; d != nil {
			sum.Completed, sum.Failed, sum.Canceled = d.Completed, d.Failed, d.Canceled
			det.Completed, det.Failed, det.Canceled = d.Completed, d.Failed, d.Canceled
			det.AvgDuration = formatSeconds(d.DurationSum, d.DurationCount)
			for _, name := range sortedKeys(d.TaskTypes) {
				tt := d.TaskTypes[name]
				det.TaskTypes = append(det.TaskTypes, taskTypeRow{name, tt.Count, formatSeconds(tt.DurationSum, tt.DurationCount)})
			}
			sort.SliceStable(det.TaskTypes, func(a, b int) bool { return det.TaskTypes[a].Count > det.TaskTypes[b].Count })
			for _, r := range topResources(d.Resources, 15) {
				r.Resource = formatResource(r.Resource, names)
				det.TopResources = append(det.TopResources, r)
			}
		}
		index[key] = len(summaries)
		summaries = append(summaries, sum)
		details[key] = det
	}

	for _, ev := range st.Events {
		if ev.EventType != eventUnexplainedWait {
			continue
		}
		key := dateKey(ev.Timestamp)
		i, ok := index[key]
		if !ok {
			continue
		}
		s := &summaries[i]
		s.EventCount++
		if len(s.EventTypes) == 0 {
			s.EventTypes = append(s.EventTypes, ev.EventType)
		}
		if severityOrder[ev.Severity] > severityOrder[s.MaxSeverity] {
			s.MaxSeverity = ev.Severity
		}
		det := details[key]
		det.Events = append(det.Events, dayEvent{ev.Timestamp, ev.EventType, ev.Severity, ev.TaskName})
		details[key] = det
	}
	return summaries, details
}

// buildMonthly aggregates the retained daily summaries by month, newest first.
func buildMonthly(st *State) []monthlySummary {
	type acc struct {
		monthlySummary
		durSum    float64
		durCount  int
		satSum    float64
		taskTypes map[string]*TaskTypeStats
	}
	months := map[string]*acc{}
	for _, d := range st.Daily {
		key := d.Date[:7]
		m, ok := months[key]
		if !ok {
			m = &acc{monthlySummary: monthlySummary{Month: key}, taskTypes: map[string]*TaskTypeStats{}}
			months[key] = m
		}
		m.Completed += d.Completed
		m.Failed += d.Failed
		m.Canceled += d.Canceled
		m.EventCount += d.EventCount
		m.durSum += d.DurationSum
		m.durCount += d.DurationCount
		m.satSum += d.SaturationSum
		m.SnapshotCount += d.PollCount
		m.PeakWorkerSaturation = math.Max(m.PeakWorkerSaturation, d.PeakSaturation)
		for name, tt := range d.TaskTypes {
			mt, ok := m.taskTypes[name]
			if !ok {
				mt = &TaskTypeStats{}
				m.taskTypes[name] = mt
			}
			mt.Count += tt.Count
			mt.DurationSum += tt.DurationSum
			mt.DurationCount += tt.DurationCount
		}
	}
	out := make([]monthlySummary, 0, len(months))
	for _, key := range sortedKeys(months) {
		m := months[key]
		m.Total = m.Completed + m.Failed + m.Canceled
		if m.durCount > 0 {
			avg := m.durSum / float64(m.durCount)
			m.AvgDuration = &avg
		}
		if m.SnapshotCount > 0 {
			m.AvgWorkerSaturation = m.satSum / float64(m.SnapshotCount)
		}
		m.TaskTypes = []monthlyTaskType{}
		for _, name := range sortedKeys(m.taskTypes) {
			tt := m.taskTypes[name]
			row := monthlyTaskType{Name: name, Count: tt.Count}
			if tt.DurationCount > 0 {
				avg := tt.DurationSum / float64(tt.DurationCount)
				row.AvgDuration = &avg
			}
			m.TaskTypes = append(m.TaskTypes, row)
		}
		sort.SliceStable(m.TaskTypes, func(a, b int) bool { return m.TaskTypes[a].Count > m.TaskTypes[b].Count })
		out = append(out, m.monthlySummary)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Month > out[j].Month })
	return out
}

// BuildPage assembles the status page data from the state.
func BuildPage(st *State, names map[string]string, now time.Time) pageData {
	ref := latestSnapshotEnd(st, now)
	page := pageData{
		ModelName:   st.Environment,
		BaseURL:     st.BaseURL,
		GeneratedAt: now.UTC().Format("2006-01-02T15:04:05.000Z"),
		Throughput:  make([]throughputRow, 0, len(timeWindows)),
	}
	for _, tw := range timeWindows {
		c, f, x := throughputForWindow(st.Snapshots, tw.Span, ref)
		page.Throughput = append(page.Throughput, throughputRow{tw.Label, c, f, x})
	}
	if latest := latestLiveSnapshot(st); latest != nil {
		formatted := *latest
		formatted.ResourceQueues = make([]ResourceQueue, 0, len(latest.ResourceQueues))
		for _, q := range latest.ResourceQueues {
			q.Resource = formatResource(q.Resource, names)
			formatted.ResourceQueues = append(formatted.ResourceQueues, q)
		}
		page.Latest = &formatted
		page.Saturation = saturation1h(st, latest, ref)
	}
	page.Incidents = buildIncidents(st.Events, now)
	for _, inc := range page.Incidents {
		if inc.Ongoing {
			page.ActiveProblems++
		}
	}
	page.DaySummaries, page.DayDetails = buildDays(st, names, now)
	page.MonthlySummaries = buildMonthly(st)
	return page
}

// RenderHTML produces the self-contained status page.
func RenderHTML(page pageData) ([]byte, error) {
	// json.Marshal escapes <, > and & so the data cannot close the <script>.
	data, err := json.Marshal(page)
	if err != nil {
		return nil, fmt.Errorf("encode page data: %w", err)
	}
	host := strings.TrimPrefix(strings.TrimPrefix(page.BaseURL, "https://"), "http://")
	out := strings.NewReplacer(
		"__TITLE__", html.EscapeString(host+" Status ("+page.ModelName+")"),
		"__BASE_URL__", html.EscapeString(page.BaseURL),
		"__GENERATED_AT__", html.EscapeString(page.GeneratedAt),
		"__DATA__", string(data),
	).Replace(pageTemplate)
	return []byte(out), nil
}

// pagePRNs lists the PRNs shown on the page.
func pagePRNs(st *State, now time.Time) []string {
	set := map[string]bool{}
	today := now.UTC().Truncate(24 * time.Hour)
	for i := range historyDays {
		if d := st.Daily[dateKey(today.AddDate(0, 0, -i))]; d != nil {
			for _, r := range topResources(d.Resources, 15) {
				set[r.Resource] = true
			}
		}
	}
	if latest := latestLiveSnapshot(st); latest != nil {
		for _, q := range latest.ResourceQueues {
			set[q.Resource] = true
		}
	}
	return sortedKeys(set)
}

// resolveNames returns display names for prns, using and refreshing the
// state's 24h cache so each PRN is looked up at most once a day.
func resolveNames(ctx context.Context, pulp PulpAPI, st *State, prns []string, now time.Time) map[string]string {
	names := map[string]string{}
	var missing []string
	for _, prn := range prns {
		if n, ok := st.ResourceNames[prn]; ok && now.Sub(n.ResolvedAt) <= resourceNameTTL {
			if n.Name != "" {
				names[prn] = n.Name
			}
			continue
		}
		missing = append(missing, prn)
	}
	if len(missing) == 0 || pulp == nil {
		return names
	}
	// Repositories are looked up per domain, so include the cached domain PRNs.
	lookup := append([]string{}, missing...)
	for prn := range st.ResourceNames {
		if strings.HasPrefix(prn, "prn:core.domain:") {
			lookup = append(lookup, prn)
		}
	}
	resolved := pulp.ResolveNames(ctx, lookup)
	for _, prn := range missing {
		st.ResourceNames[prn] = ResourceName{Name: resolved[prn], ResolvedAt: now}
		if resolved[prn] != "" {
			names[prn] = resolved[prn]
		}
	}
	return names
}

// statusPage resolves names and renders the page for st.
func statusPage(ctx context.Context, pulp PulpAPI, st *State, now time.Time) ([]byte, error) {
	names := resolveNames(ctx, pulp, st, pagePRNs(st, now), now)
	return RenderHTML(BuildPage(st, names, now))
}

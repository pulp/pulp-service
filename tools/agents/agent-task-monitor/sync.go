package main

import (
	"context"
	"fmt"
	"os"
	"sort"
	"time"
)

const (
	maxUnexplainedEvents = 10
	// maxStoredResources caps the per-day resource map; the page shows the top 15.
	maxStoredResources = 50
	// dailyRetention keeps daily summaries long enough for the monthly table.
	dailyRetention = 400 * 24 * time.Hour
)

// SyncOptions controls the sync window.
type SyncOptions struct {
	Start  *time.Time
	End    *time.Time
	MaxGap time.Duration
	Now    time.Time
}

// SyncResult describes one sync, for metrics, logging and Jira.
type SyncResult struct {
	WindowStart     time.Time      `json:"window_start"`
	WindowEnd       time.Time      `json:"window_end"`
	GapTruncated    bool           `json:"gap_truncated,omitempty"`
	Finished        int            `json:"finished"`
	Completed       int            `json:"completed"`
	Failed          int            `json:"failed"`
	Canceled        int            `json:"canceled"`
	Waiting         int            `json:"waiting"`
	Running         int            `json:"running"`
	OnlineWorkers   int            `json:"online_workers"`
	Saturation      float64        `json:"worker_saturation"`
	CapacityBlocked int            `json:"capacity_blocked"`
	ResourceBlocked int            `json:"resource_blocked"`
	QueueBlocked    int            `json:"queue_blocked"`
	UnexplainedWait int            `json:"unexplained_wait"`
	AnomalousQueues int            `json:"anomalous_queues"`
	NewEvents       int            `json:"new_events"`
	CompletedByType map[string]int `json:"-"`
	FailedByType    map[string]int `json:"-"`
}

func gridFloor(t time.Time) time.Time {
	return t.UTC().Truncate(bucketSize)
}

// syncWindow picks [start, end). By default end is the last 5-minute grid
// boundary and start is where the previous sync stopped, so consecutive runs
// cover time without gaps or overlaps.
func syncWindow(st *State, opts SyncOptions) (start, end time.Time, truncated bool) {
	end = gridFloor(opts.Now)
	if opts.End != nil {
		end = opts.End.UTC()
	}
	start = end.Add(-bucketSize)
	switch {
	case opts.Start != nil:
		start = opts.Start.UTC()
	case st.LastWindowEnd != nil:
		start = st.LastWindowEnd.UTC()
		if opts.MaxGap > 0 && end.Sub(start) > opts.MaxGap {
			start = end.Add(-opts.MaxGap)
			truncated = true
		}
	}
	return start, end, truncated
}

// dedupeTasks drops tasks already seen (a task can move between queries).
func dedupeTasks(seen map[string]bool, tasks []Task, want string) []Task {
	var out []Task
	for _, t := range tasks {
		if seen[t.Href] {
			continue
		}
		seen[t.Href] = true
		if want == "" || t.State == want {
			out = append(out, t)
		}
	}
	return out
}

func isFinished(state string) bool {
	return state == stateCompleted || state == stateFailed || state == stateCanceled
}

// Sync is one polling cycle of the monitor. It takes a time window, asks Pulp what happened
// during it and what's going on right now, classifies the waiting tasks, and folds everything
// into the persisted State. It returns a SyncResult used for metrics, logs and Jira.
func Sync(ctx context.Context, pulp PulpAPI, st *State, opts SyncOptions) (*SyncResult, error) {
	start, end, truncated := syncWindow(st, opts)
	if truncated {
		fmt.Fprintf(os.Stderr, "[sync] warning: last sync ended at %s, more than %s ago; only syncing from %s (use -mode backfill to fill the gap)\n",
			st.LastWindowEnd.UTC().Format(time.RFC3339), opts.MaxGap, start.Format(time.RFC3339))
	}
	res := &SyncResult{WindowStart: start, WindowEnd: end, GapTruncated: truncated,
		CompletedByType: map[string]int{}, FailedByType: map[string]int{}}

	var finishedRaw []Task
	if start.Before(end) {
		var err error
		if finishedRaw, err = pulp.FinishedTasks(ctx, start, end); err != nil {
			return nil, err
		}
	} else {
		fmt.Fprintf(os.Stderr, "[sync] window %s..%s already synced; refreshing the live poll only\n", start.Format(time.RFC3339), end.Format(time.RFC3339))
	}
	runningRaw, err := pulp.TasksInState(ctx, stateRunning)
	if err != nil {
		return nil, err
	}
	waitingRaw, err := pulp.TasksInState(ctx, stateWaiting)
	if err != nil {
		return nil, err
	}
	workers, err := pulp.OnlineWorkers(ctx)
	if err != nil {
		return nil, err
	}

	seen := map[string]bool{}
	var finished []Task
	for _, t := range dedupeTasks(seen, finishedRaw, "") {
		if isFinished(t.State) {
			finished = append(finished, t)
		}
	}
	running := dedupeTasks(seen, runningRaw, stateRunning)
	waiting := dedupeTasks(seen, waitingRaw, stateWaiting)

	res.Running, res.Waiting, res.OnlineWorkers = len(running), len(waiting), workers
	if workers > 0 {
		res.Saturation = float64(len(running)) / float64(workers)
	}

	cls := classifyWaitTasks(running, waiting, workers, len(running), end)
	res.CapacityBlocked, res.ResourceBlocked = cls.CapacityBlocked, cls.ResourceBlocked
	res.QueueBlocked, res.UnexplainedWait = cls.QueueBlocked, cls.UnexplainedWait
	for _, q := range cls.ResourceQueues {
		if q.Status == "anomaly" {
			res.AnomalousQueues++
		}
	}

	events := buildEvents(cls, workers, len(running), start, opts.Now)
	res.NewEvents = len(events)
	st.Events = append(st.Events, events...)
	for _, ev := range events {
		dailyFor(st, dateKey(ev.Timestamp)).EventCount++
	}

	for _, t := range finished {
		switch t.State {
		case stateCompleted:
			res.Completed++
			res.CompletedByType[t.Name]++
		case stateFailed:
			res.Failed++
			res.FailedByType[t.Name]++
		case stateCanceled:
			res.Canceled++
		}
	}
	res.Finished = len(finished)

	finishedTime := func(t Task) time.Time {
		if t.FinishedAt != nil {
			return *t.FinishedAt
		}
		return start
	}
	addFinishedToSnapshots(st, finished, finishedTime)
	for _, t := range finished {
		addTaskToDaily(dailyFor(st, dateKey(finishedTime(t))), t)
	}

	live := Snapshot{
		Live:                 true,
		WaitingCount:         len(waiting),
		RunningCount:         len(running),
		OnlineWorkers:        workers,
		WorkerSaturation:     res.Saturation,
		CapacityBlockedTasks: cls.CapacityBlocked,
		ResourceBlockedTasks: cls.ResourceBlocked,
		QueueBlockedTasks:    cls.QueueBlocked,
		UnexplainedWaitTasks: cls.UnexplainedWait,
		ResourceQueues:       cls.ResourceQueues,
	}
	liveStart := gridFloor(end.Add(-time.Nanosecond))
	setLiveSnapshot(st, liveStart, live)
	d := dailyFor(st, dateKey(liveStart))
	d.PollCount++
	d.SaturationSum += res.Saturation
	if res.Saturation > d.PeakSaturation {
		d.PeakSaturation = res.Saturation
	}

	if st.LastWindowEnd == nil || end.After(*st.LastWindowEnd) {
		e := end
		st.LastWindowEnd = &e
	}
	return res, nil
}

// buildEvents emits up to 10 unexplained_wait events (the third and later are
// critical) and a worker_saturation event when tasks wait on busy workers.
func buildEvents(cls WaitClassification, workers, running int, windowStart, now time.Time) []Event {
	var events []Event
	for i, c := range cls.Unexplained {
		if i >= maxUnexplainedEvents {
			break
		}
		severity := severityWarning
		if i+1 >= 3 {
			severity = severityCritical
		}
		events = append(events, Event{
			Timestamp:          now.UTC(),
			EventType:          eventUnexplainedWait,
			TaskHref:           c.TaskHref,
			TaskName:           c.TaskName,
			RequestedResources: c.RequestedResources,
			PollWindowStart:    windowStart,
			Severity:           severity,
		})
	}
	if workers > 0 && cls.CapacityBlocked > 0 {
		ratio := float64(running) / float64(workers)
		severity := severityInfo
		if ratio > 0.95 {
			severity = severityCritical
		} else if ratio > 0.8 {
			severity = severityWarning
		}
		events = append(events, Event{
			Timestamp:          now.UTC(),
			EventType:          eventWorkerSaturation,
			RequestedResources: []string{},
			PollWindowStart:    windowStart,
			Severity:           severity,
		})
	}
	return events
}

func dailyFor(st *State, date string) *DailySummary {
	d, ok := st.Daily[date]
	if !ok {
		d = newDailySummary(date)
		st.Daily[date] = d
	}
	return d
}

func addTaskToDaily(d *DailySummary, t Task) {
	switch t.State {
	case stateCompleted:
		d.Completed++
	case stateFailed:
		d.Failed++
	case stateCanceled:
		d.Canceled++
	}
	tt, ok := d.TaskTypes[t.Name]
	if !ok {
		tt = &TaskTypeStats{}
		d.TaskTypes[t.Name] = tt
	}
	tt.Count++
	if dur, ok := t.Duration(); ok {
		tt.DurationSum += dur
		tt.DurationCount++
		d.DurationSum += dur
		d.DurationCount++
	}
	for _, r := range t.Exclusive {
		resourceFor(d, r).Exclusive++
	}
	for _, r := range t.Shared {
		resourceFor(d, r).Shared++
	}
}

func resourceFor(d *DailySummary, r string) *ResourceCounts {
	rc, ok := d.Resources[r]
	if !ok {
		rc = &ResourceCounts{}
		d.Resources[r] = rc
	}
	return rc
}

func snapshotIndex(st *State, start time.Time) int {
	for i := range st.Snapshots {
		if st.Snapshots[i].WindowStart.Equal(start) {
			return i
		}
	}
	return -1
}

func snapshotFor(st *State, start time.Time) *Snapshot {
	if i := snapshotIndex(st, start); i >= 0 {
		return &st.Snapshots[i]
	}
	st.Snapshots = append(st.Snapshots, Snapshot{WindowStart: start, WindowEnd: start.Add(bucketSize)})
	return &st.Snapshots[len(st.Snapshots)-1]
}

// addFinishedToSnapshots adds finished tasks to their 5-minute buckets. Sync
// windows never overlap, so adding (rather than overwriting) is exact.
func addFinishedToSnapshots(st *State, finished []Task, finishedTime func(Task) time.Time) {
	index := make(map[int64]int, len(st.Snapshots))
	for i, s := range st.Snapshots {
		index[s.WindowStart.UnixNano()] = i
	}
	for _, t := range finished {
		bucket := gridFloor(finishedTime(t))
		i, ok := index[bucket.UnixNano()]
		if !ok {
			st.Snapshots = append(st.Snapshots, Snapshot{WindowStart: bucket, WindowEnd: bucket.Add(bucketSize)})
			i = len(st.Snapshots) - 1
			index[bucket.UnixNano()] = i
		}
		s := &st.Snapshots[i]
		switch t.State {
		case stateCompleted:
			s.CompletedCount++
		case stateFailed:
			s.FailedCount++
		case stateCanceled:
			s.CanceledCount++
		}
		s.TotalTasksPolled++
	}
}

// setLiveSnapshot stores the poll on its bucket, keeping the bucket's finished
// counts. Older snapshots drop their resource queues: only the latest is shown.
func setLiveSnapshot(st *State, start time.Time, live Snapshot) {
	for i := range st.Snapshots {
		st.Snapshots[i].ResourceQueues = nil
	}
	s := snapshotFor(st, start)
	live.WindowStart, live.WindowEnd = s.WindowStart, s.WindowEnd
	live.CompletedCount, live.FailedCount, live.CanceledCount = s.CompletedCount, s.FailedCount, s.CanceledCount
	live.TotalTasksPolled = s.TotalTasksPolled
	*s = live
}

// Prune drops data past retention, caps per-day resource maps and expires the
// resource name cache.
func Prune(st *State, now time.Time, retention time.Duration) {
	cutoff := now.Add(-retention)
	snaps := st.Snapshots[:0]
	for _, s := range st.Snapshots {
		if s.WindowEnd.After(cutoff) {
			snaps = append(snaps, s)
		}
	}
	st.Snapshots = snaps
	sort.Slice(st.Snapshots, func(i, j int) bool { return st.Snapshots[i].WindowStart.Before(st.Snapshots[j].WindowStart) })

	events := st.Events[:0]
	for _, e := range st.Events {
		if e.Timestamp.After(cutoff) {
			events = append(events, e)
		}
	}
	st.Events = events

	oldestDay := dateKey(now.Add(-dailyRetention))
	for date, d := range st.Daily {
		if date < oldestDay {
			delete(st.Daily, date)
			continue
		}
		capResources(d, maxStoredResources)
	}

	for prn, n := range st.ResourceNames {
		if now.Sub(n.ResolvedAt) > resourceNameTTL {
			delete(st.ResourceNames, prn)
		}
	}
}

func capResources(d *DailySummary, n int) {
	if len(d.Resources) <= n {
		return
	}
	for _, r := range topResources(d.Resources, len(d.Resources))[n:] {
		delete(d.Resources, r.Resource)
	}
}

type resourceRow struct {
	Resource  string `json:"resource"`
	Exclusive int    `json:"exclusive"`
	Shared    int    `json:"shared"`
}

func topResources(m map[string]*ResourceCounts, n int) []resourceRow {
	rows := make([]resourceRow, 0, len(m))
	for r, c := range m {
		rows = append(rows, resourceRow{Resource: r, Exclusive: c.Exclusive, Shared: c.Shared})
	}
	sort.Slice(rows, func(i, j int) bool {
		ti, tj := rows[i].Exclusive+rows[i].Shared, rows[j].Exclusive+rows[j].Shared
		if ti != tj {
			return ti > tj
		}
		return rows[i].Resource < rows[j].Resource
	})
	if len(rows) > n {
		rows = rows[:n]
	}
	return rows
}

// Backfill re-fetches finished tasks one UTC day at a time from start to end
// and replaces the finished-task counts of the affected snapshots and daily
// summaries, so re-running it never double counts. Live poll data is kept.
// Days are always replaced whole: end is rounded up to the end of its day,
// capped at now.
func Backfill(ctx context.Context, pulp PulpAPI, st *State, start, end, now time.Time) error {
	dayStart := start.UTC().Truncate(24 * time.Hour)
	end = end.UTC()
	if !end.Equal(end.Truncate(24 * time.Hour)) {
		end = end.Truncate(24 * time.Hour).Add(24 * time.Hour)
	}
	if end.After(now) {
		end = now.UTC()
	}
	for ; dayStart.Before(end); dayStart = dayStart.Add(24 * time.Hour) {
		dayEnd := dayStart.Add(24 * time.Hour)
		if dayEnd.After(end) {
			dayEnd = end
		}
		fmt.Fprintf(os.Stderr, "[backfill] %s\n", dateKey(dayStart))
		tasks, err := pulp.FinishedTasks(ctx, dayStart, dayEnd)
		if err != nil {
			return fmt.Errorf("backfill %s: %w", dateKey(dayStart), err)
		}

		for i := range st.Snapshots {
			s := &st.Snapshots[i]
			if !s.WindowStart.Before(dayStart) && s.WindowStart.Before(dayEnd) {
				s.CompletedCount, s.FailedCount, s.CanceledCount, s.TotalTasksPolled = 0, 0, 0, 0
			}
		}
		old := st.Daily[dateKey(dayStart)]
		d := newDailySummary(dateKey(dayStart))
		if old != nil {
			d.EventCount, d.PeakSaturation, d.SaturationSum, d.PollCount = old.EventCount, old.PeakSaturation, old.SaturationSum, old.PollCount
		}
		st.Daily[d.Date] = d

		var finished []Task
		for _, t := range tasks {
			if t.FinishedAt != nil && isFinished(t.State) {
				finished = append(finished, t)
			}
		}
		addFinishedToSnapshots(st, finished, func(t Task) time.Time { return *t.FinishedAt })
		for _, t := range finished {
			addTaskToDaily(d, t)
		}
	}
	if st.LastWindowEnd == nil || end.After(*st.LastWindowEnd) {
		st.LastWindowEnd = &end
	}
	return nil
}

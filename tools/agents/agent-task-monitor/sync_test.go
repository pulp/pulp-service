package main

import (
	"context"
	"errors"
	"testing"
	"time"
)

type fakePulp struct {
	finished      []Task
	running       []Task
	waiting       []Task
	workers       int
	workersErr    error
	finishedCalls [][2]time.Time
}

func (f *fakePulp) FinishedTasks(_ context.Context, start, end time.Time) ([]Task, error) {
	f.finishedCalls = append(f.finishedCalls, [2]time.Time{start, end})
	var out []Task
	for _, t := range f.finished {
		if t.FinishedAt != nil && !t.FinishedAt.Before(start) && t.FinishedAt.Before(end) {
			out = append(out, t)
		}
	}
	return out, nil
}

func (f *fakePulp) TasksInState(_ context.Context, state string) ([]Task, error) {
	if state == stateRunning {
		return f.running, nil
	}
	return f.waiting, nil
}

func (f *fakePulp) OnlineWorkers(context.Context) (int, error) { return f.workers, f.workersErr }

func (f *fakePulp) ResolveNames(_ context.Context, prns []string) map[string]string {
	return map[string]string{}
}

func finishedTask(href, name, state, started, finished string, exclusive ...string) Task {
	s, f := mustTime(started), mustTime(finished)
	return Task{Href: href, Name: name, State: state, Created: s, StartedAt: &s, FinishedAt: &f, Exclusive: exclusive}
}

func TestSyncWindowDefaults(t *testing.T) {
	now := mustTime("2026-08-25T16:03:10Z")
	st := newState("env", "https://x")

	start, end, _ := syncWindow(st, SyncOptions{Now: now})
	if !start.Equal(mustTime("2026-08-25T15:55:00Z")) || !end.Equal(mustTime("2026-08-25T16:00:00Z")) {
		t.Errorf("fresh window = %s..%s", start, end)
	}

	last := mustTime("2026-08-25T15:40:00Z")
	st.LastWindowEnd = &last
	start, end, truncated := syncWindow(st, SyncOptions{Now: now, MaxGap: time.Hour})
	if !start.Equal(last) || !end.Equal(mustTime("2026-08-25T16:00:00Z")) || truncated {
		t.Errorf("gap-fill window = %s..%s truncated=%v", start, end, truncated)
	}

	start, _, truncated = syncWindow(st, SyncOptions{Now: now, MaxGap: 10 * time.Minute})
	if !start.Equal(mustTime("2026-08-25T15:50:00Z")) || !truncated {
		t.Errorf("truncated window start = %s truncated=%v", start, truncated)
	}
}

func TestSyncBuildsSnapshotsDailyAndEvents(t *testing.T) {
	now := mustTime("2026-08-25T16:01:00Z")
	pulp := &fakePulp{
		workers: 4,
		finished: []Task{
			finishedTask("/t/1/", "sync", stateCompleted, "2026-08-25T15:50:00Z", "2026-08-25T15:55:30Z", "prn:rpm.rpmrepository:r1"),
			finishedTask("/t/2/", "sync", stateFailed, "2026-08-25T15:58:00Z", "2026-08-25T15:58:10Z", "prn:rpm.rpmrepository:r1"),
			finishedTask("/t/3/", "publish", stateCanceled, "2026-08-25T15:59:00Z", "2026-08-25T15:59:01Z"),
		},
		running: []Task{{Href: "/r/1/", State: stateRunning, Exclusive: []string{"busy"}}},
		waiting: []Task{
			// Stuck head: free workers, nothing holds "stuck".
			{Href: "/w/1/", Name: "sync", State: stateWaiting, Created: mustTime("2026-08-25T15:30:00Z"), Exclusive: []string{"stuck"}},
			// Legitimately blocked by the running task.
			{Href: "/w/2/", Name: "sync", State: stateWaiting, Created: mustTime("2026-08-25T15:30:00Z"), Exclusive: []string{"busy"}},
		},
	}
	st := newState("env", "https://x")
	last := mustTime("2026-08-25T15:55:00Z")
	st.LastWindowEnd = &last

	res, err := Sync(context.Background(), pulp, st, SyncOptions{Now: now, MaxGap: time.Hour})
	if err != nil {
		t.Fatal(err)
	}
	if res.Completed != 1 || res.Failed != 1 || res.Canceled != 1 || res.UnexplainedWait != 1 || res.ResourceBlocked != 1 {
		t.Errorf("result = %+v", res)
	}
	if res.Saturation != 0.25 || res.CompletedByType["sync"] != 1 || res.FailedByType["sync"] != 1 {
		t.Errorf("saturation/by-type = %v %v %v", res.Saturation, res.CompletedByType, res.FailedByType)
	}
	if !st.LastWindowEnd.Equal(mustTime("2026-08-25T16:00:00Z")) {
		t.Errorf("last window end = %s", st.LastWindowEnd)
	}

	// One bucket 15:55-16:00 holds all three tasks and the live poll.
	if len(st.Snapshots) != 1 {
		t.Fatalf("snapshots = %+v", st.Snapshots)
	}
	s := st.Snapshots[0]
	if !s.Live || s.CompletedCount != 1 || s.FailedCount != 1 || s.CanceledCount != 1 || s.WaitingCount != 2 || s.OnlineWorkers != 4 {
		t.Errorf("snapshot = %+v", s)
	}
	if len(s.ResourceQueues) != 2 {
		t.Errorf("queues = %+v", s.ResourceQueues)
	}

	d := st.Daily["2026-08-25"]
	if d == nil || d.Completed != 1 || d.Failed != 1 || d.Canceled != 1 || d.TaskTypes["sync"].Count != 2 || d.Resources["prn:rpm.rpmrepository:r1"].Exclusive != 2 {
		t.Fatalf("daily = %+v", d)
	}
	if d.PollCount != 1 || d.EventCount != 1 {
		t.Errorf("daily poll/event = %d/%d", d.PollCount, d.EventCount)
	}
	if len(st.Events) != 1 || st.Events[0].EventType != eventUnexplainedWait || st.Events[0].TaskHref != "/w/1/" {
		t.Errorf("events = %+v", st.Events)
	}

	// A second run in the same 5-minute slot refreshes the live poll only.
	pulp.waiting = nil
	if _, err := Sync(context.Background(), pulp, st, SyncOptions{Now: now.Add(time.Minute), MaxGap: time.Hour}); err != nil {
		t.Fatal(err)
	}
	if len(pulp.finishedCalls) != 1 {
		t.Errorf("finished tasks fetched %d times, want 1", len(pulp.finishedCalls))
	}
	s = st.Snapshots[0]
	if s.CompletedCount != 1 || s.WaitingCount != 0 || len(s.ResourceQueues) != 0 {
		t.Errorf("snapshot after refresh = %+v", s)
	}
	if st.Daily["2026-08-25"].Completed != 1 {
		t.Errorf("daily double counted: %+v", st.Daily["2026-08-25"])
	}
}

func TestSyncFailsWhenStatusFails(t *testing.T) {
	pulp := &fakePulp{workersErr: errors.New("boom")}
	st := newState("env", "https://x")
	if _, err := Sync(context.Background(), pulp, st, SyncOptions{Now: time.Now()}); err == nil {
		t.Fatal("expected an error")
	}
	if st.LastWindowEnd != nil || len(st.Snapshots) != 0 {
		t.Error("state changed on failure")
	}
}

func TestBuildEventsSeverity(t *testing.T) {
	cls := WaitClassification{CapacityBlocked: 1}
	for range 12 {
		cls.Unexplained = append(cls.Unexplained, UnexplainedWait{TaskHref: "/t/"})
	}
	events := buildEvents(cls, 10, 9, time.Now(), time.Now())
	if len(events) != maxUnexplainedEvents+1 {
		t.Fatalf("got %d events", len(events))
	}
	if events[0].Severity != severityWarning || events[1].Severity != severityWarning || events[2].Severity != severityCritical {
		t.Errorf("severities = %s %s %s", events[0].Severity, events[1].Severity, events[2].Severity)
	}
	last := events[len(events)-1]
	if last.EventType != eventWorkerSaturation || last.Severity != severityWarning {
		t.Errorf("saturation event = %+v", last)
	}
}

func TestBackfillIsIdempotentAndKeepsLiveData(t *testing.T) {
	now := mustTime("2026-08-26T10:00:00Z")
	pulp := &fakePulp{finished: []Task{
		finishedTask("/t/1/", "sync", stateCompleted, "2026-08-25T10:00:00Z", "2026-08-25T10:01:00Z"),
		finishedTask("/t/2/", "sync", stateCompleted, "2026-08-25T10:02:00Z", "2026-08-25T10:03:00Z"),
	}}
	st := newState("env", "https://x")
	// A live poll recorded earlier in the same bucket, with a stale count.
	st.Snapshots = []Snapshot{{WindowStart: mustTime("2026-08-25T10:00:00Z"), WindowEnd: mustTime("2026-08-25T10:05:00Z"),
		CompletedCount: 7, Live: true, OnlineWorkers: 5, WorkerSaturation: 0.4}}
	st.Daily["2026-08-25"] = &DailySummary{Date: "2026-08-25", Completed: 99, PollCount: 3, EventCount: 2,
		TaskTypes: map[string]*TaskTypeStats{}, Resources: map[string]*ResourceCounts{}}

	for range 2 {
		if err := Backfill(context.Background(), pulp, st, mustTime("2026-08-25T06:00:00Z"), mustTime("2026-08-25T12:00:00Z"), now); err != nil {
			t.Fatal(err)
		}
	}
	// Days are fetched whole.
	if got := pulp.finishedCalls[0]; !got[0].Equal(mustTime("2026-08-25T00:00:00Z")) || !got[1].Equal(mustTime("2026-08-26T00:00:00Z")) {
		t.Errorf("fetched %s..%s", got[0], got[1])
	}
	s := st.Snapshots[0]
	if s.CompletedCount != 2 || !s.Live || s.OnlineWorkers != 5 {
		t.Errorf("snapshot = %+v", s)
	}
	d := st.Daily["2026-08-25"]
	if d.Completed != 2 || d.PollCount != 3 || d.EventCount != 2 || d.TaskTypes["sync"].Count != 2 {
		t.Errorf("daily = %+v", d)
	}
	if !st.LastWindowEnd.Equal(mustTime("2026-08-26T00:00:00Z")) {
		t.Errorf("last window end = %s", st.LastWindowEnd)
	}
}

func TestPrune(t *testing.T) {
	now := mustTime("2026-08-25T00:00:00Z")
	st := newState("env", "https://x")
	st.Snapshots = []Snapshot{
		{WindowStart: now.Add(-40 * 24 * time.Hour), WindowEnd: now.Add(-40*24*time.Hour + bucketSize)},
		{WindowStart: now.Add(-time.Hour), WindowEnd: now.Add(-time.Hour + bucketSize)},
	}
	st.Events = []Event{{Timestamp: now.Add(-31 * 24 * time.Hour)}, {Timestamp: now.Add(-time.Hour)}}
	st.Daily["2025-01-01"] = newDailySummary("2025-01-01")
	st.Daily["2026-08-01"] = newDailySummary("2026-08-01")
	for i := range maxStoredResources + 10 {
		st.Daily["2026-08-01"].Resources[string(rune('a'+i))] = &ResourceCounts{Exclusive: i}
	}
	st.ResourceNames["old"] = ResourceName{Name: "x", ResolvedAt: now.Add(-48 * time.Hour)}

	Prune(st, now, 30*24*time.Hour)

	if len(st.Snapshots) != 1 || len(st.Events) != 1 {
		t.Errorf("snapshots=%d events=%d", len(st.Snapshots), len(st.Events))
	}
	if _, ok := st.Daily["2025-01-01"]; ok {
		t.Error("old daily summary kept")
	}
	if n := len(st.Daily["2026-08-01"].Resources); n != maxStoredResources {
		t.Errorf("resources kept = %d", n)
	}
	if _, ok := st.ResourceNames["old"]; ok {
		t.Error("stale name kept")
	}
}

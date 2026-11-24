package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestLocalStoreConflicts(t *testing.T) {
	ctx := context.Background()
	store := &localStore{dir: t.TempDir()}

	st, version, err := LoadState(ctx, store, "env", "https://x")
	if err != nil || version != "" {
		t.Fatalf("fresh load: %v %q", err, version)
	}
	v1, err := SaveState(ctx, store, st, version)
	if err != nil {
		t.Fatal(err)
	}
	// A second writer that also started from "no state" must lose.
	if _, err := SaveState(ctx, store, st, ""); !errors.Is(err, errConflict) {
		t.Fatalf("err = %v, want conflict", err)
	}
	st.Snapshots = append(st.Snapshots, Snapshot{WindowStart: time.Now()})
	v2, err := SaveState(ctx, store, st, v1)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := SaveState(ctx, store, st, v1); !errors.Is(err, errConflict) {
		t.Fatalf("stale version accepted: %v", err)
	}
	loaded, v3, err := LoadState(ctx, store, "env", "https://x")
	if err != nil || v3 != v2 || len(loaded.Snapshots) != 1 {
		t.Fatalf("reload: %v %q %d", err, v3, len(loaded.Snapshots))
	}
	if _, _, err := LoadState(ctx, store, "other", "https://x"); err != nil {
		t.Fatalf("other env should start fresh: %v", err)
	}
}

func TestPulpClient(t *testing.T) {
	var statusCalls int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if u, p, ok := r.BasicAuth(); !ok || u != "user" || p != "pass" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		switch r.URL.Path {
		case "/api/pulp/admin/tasks/":
			if r.URL.Query().Get("fields") == "" {
				t.Error("tasks query without fields")
			}
			offset, _ := strconv.Atoi(r.URL.Query().Get("offset"))
			// Two pages: a full one and a short one.
			n := taskPageSize
			next := any("more")
			if offset > 0 {
				n, next = 3, nil
			}
			var results []map[string]any
			for i := range n {
				results = append(results, map[string]any{
					"pulp_href":                 fmt.Sprintf("/t/%d-%d/", offset, i),
					"state":                     "completed",
					"name":                      "sync",
					"pulp_created":              "2026-08-25T10:00:00.123456Z",
					"started_at":                "2026-08-25T10:00:01Z",
					"finished_at":               "2026-08-25T10:00:03Z",
					"reserved_resources_record": []any{"prn:rpm.rpmrepository:r", "shared:prn:core.domain:d"},
				})
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"next": next, "results": results})
		case "/api/pulp/api/v3/status/":
			statusCalls++
			if statusCalls == 1 {
				w.WriteHeader(http.StatusBadGateway) // retried
				return
			}
			_, _ = io.WriteString(w, `{"online_workers":[{},{},{}]}`)
		default:
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()

	c := newPulpClient(srv.URL, "/api/pulp/admin/tasks/", "/api/pulp/api/v3/status/", "user", "pass")
	tasks, err := c.FinishedTasks(context.Background(), mustTime("2026-08-25T10:00:00Z"), mustTime("2026-08-25T11:00:00Z"))
	if err != nil {
		t.Fatal(err)
	}
	if len(tasks) != taskPageSize+3 {
		t.Fatalf("got %d tasks", len(tasks))
	}
	tk := tasks[0]
	if d, ok := tk.Duration(); !ok || d != 2 || len(tk.Exclusive) != 1 || tk.Shared[0] != "prn:core.domain:d" {
		t.Errorf("task = %+v", tk)
	}

	workers, err := c.OnlineWorkers(context.Background())
	if err != nil || workers != 3 {
		t.Errorf("workers = %d, %v", workers, err)
	}

	bad := newPulpClient(srv.URL, "/api/pulp/admin/tasks/", "/api/pulp/api/v3/status/", "user", "wrong")
	if _, err := bad.OnlineWorkers(context.Background()); err == nil || !strings.Contains(err.Error(), "401") {
		t.Errorf("err = %v", err)
	}
}

func TestOTLPPayload(t *testing.T) {
	now := mustTime("2026-08-25T12:00:00Z")
	res := &SyncResult{Waiting: 2, Running: 3, OnlineWorkers: 4, Saturation: 0.75, UnexplainedWait: 1,
		CompletedByType: map[string]int{"b": 1, "a": 2}, FailedByType: map[string]int{"a": 1}}

	payload, _ := json.Marshal(buildOTLPPayload(res, "pulp-tasks-prod", now, true))
	s := string(payload)
	for _, want := range []string{
		`"name":"pulp_monitor_last_success_timestamp"`, `"asDouble":1787659200`,
		`"name":"pulp_worker_saturation"`, `"asDouble":0.75`,
		`"name":"pulp_tasks_completed_total"`, `"stringValue":"pulp-tasks-prod"`, `"key":"task_type"`,
		`"name":"pulp_unexplained_wait_tasks"`,
	} {
		if !strings.Contains(s, want) {
			t.Errorf("payload missing %s", want)
		}
	}
	noSuccess, _ := json.Marshal(buildOTLPPayload(res, "pulp-tasks-prod", now, false))
	if strings.Contains(string(noSuccess), "last_success") {
		t.Error("last_success included on failure")
	}

	var got []byte
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/metrics" {
			http.NotFound(w, r)
			return
		}
		got, _ = io.ReadAll(r.Body)
	}))
	defer srv.Close()
	if err := pushMetrics(context.Background(), srv.URL+"/", buildOTLPPayload(res, "e", now, true)); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(got), "resourceMetrics") {
		t.Errorf("pushed %s", got)
	}
	if err := pushMetrics(context.Background(), srv.URL+"/nope", buildOTLPPayload(res, "e", now, true)); err == nil {
		t.Error("expected push error")
	}
}

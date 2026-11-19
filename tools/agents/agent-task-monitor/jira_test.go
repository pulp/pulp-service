package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/tmc/langchaingo/llms"

	mcpClient "github.com/pulp/pulp-service/tools/agents/shared/mcp-client"
)

var testJiraCfg = JiraConfig{
	Project: "PULP", IssueType: "Bug", CommentGroup: "Red Hat Employee",
	MinDuration: 15 * time.Minute, UpdateInterval: time.Hour, MaxCreatesPerDay: 2,
}

func incident(start, end string, ongoing bool) Incident {
	return Incident{Start: mustTime(start), End: mustTime(end), Ongoing: ongoing, Count: 3}
}

func TestDecideJiraAction(t *testing.T) {
	now := mustTime("2026-08-25T12:00:00Z")
	start := mustTime("2026-08-25T11:30:00Z")
	recent := now.Add(-10 * time.Minute)
	old := now.Add(-2 * time.Hour)
	other := mustTime("2026-08-25T09:00:00Z")

	cases := []struct {
		name      string
		js        JiraState
		incidents []Incident
		want      jiraAction
	}{
		{"no incidents", JiraState{}, nil, jiraNone},
		{"short incident", JiraState{}, []Incident{incident("2026-08-25T11:50:00Z", "2026-08-25T11:58:00Z", true)}, jiraNone},
		{"persistent incident", JiraState{}, []Incident{incident("2026-08-25T11:30:00Z", "2026-08-25T11:58:00Z", true)}, jiraOpen},
		{"tracked, recently updated", JiraState{IssueKey: "PULP-1", IncidentStart: &start, LastUpdate: &recent},
			[]Incident{incident("2026-08-25T11:30:00Z", "2026-08-25T11:58:00Z", true)}, jiraNone},
		{"tracked, update due", JiraState{IssueKey: "PULP-1", IncidentStart: &start, LastUpdate: &old},
			[]Incident{incident("2026-08-25T11:30:00Z", "2026-08-25T11:58:00Z", true)}, jiraUpdate},
		{"tracked, ended", JiraState{IssueKey: "PULP-1", IncidentStart: &start, LastUpdate: &recent},
			[]Incident{incident("2026-08-25T11:30:00Z", "2026-08-25T11:40:00Z", false)}, jiraResolve},
		{"tracked, aged out", JiraState{IssueKey: "PULP-1", IncidentStart: &other}, nil, jiraResolve},
		{"tracked, replaced by new incident", JiraState{IssueKey: "PULP-1", IncidentStart: &other},
			[]Incident{incident("2026-08-25T11:30:00Z", "2026-08-25T11:58:00Z", true)}, jiraResolve},
	}
	for _, c := range cases {
		got, _ := decideJiraAction(c.js, c.incidents, now, testJiraCfg)
		if got != c.want {
			t.Errorf("%s: got %q, want %q", c.name, got, c.want)
		}
	}
}

// fakeJira records requests and serves canned search results.
type fakeJira struct {
	mu       sync.Mutex
	open     []string
	requests []string
	bodies   []map[string]any
}

func (f *fakeJira) handler(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	body, _ := io.ReadAll(r.Body)
	var parsed map[string]any
	_ = json.Unmarshal(body, &parsed)
	f.requests = append(f.requests, r.Method+" "+r.URL.Path)
	f.bodies = append(f.bodies, parsed)
	switch {
	case r.URL.Path == "/rest/api/3/search/jql":
		var issues []map[string]any
		for _, k := range f.open {
			issues = append(issues, map[string]any{"key": k, "fields": map[string]any{"summary": "s", "status": map[string]any{"name": "New"}}})
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"issues": issues})
	case r.URL.Path == "/rest/api/3/issue" && r.Method == http.MethodPost:
		_, _ = w.Write([]byte(`{"key":"PULP-99"}`))
	case strings.HasSuffix(r.URL.Path, "/comment") && r.Method == http.MethodPost:
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{}`))
	case strings.HasSuffix(r.URL.Path, "/comment"):
		_, _ = w.Write([]byte(`{"comments":[{"created":"2026-08-25","body":{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"earlier"}]}]}}]}`))
	default:
		http.NotFound(w, r)
	}
}

func newTestSession(t *testing.T, open []string, js *JiraState) (*jiraSession, *fakeJira) {
	t.Helper()
	fj := &fakeJira{open: open}
	srv := httptest.NewServer(http.HandlerFunc(fj.handler))
	t.Cleanup(srv.Close)
	return newJiraSession(newJiraClient(srv.URL, "u", "t"), testJiraCfg, "pulp-tasks-prod", js, mustTime("2026-08-25T12:00:00Z")), fj
}

func TestJiraGuardrails(t *testing.T) {
	ctx := context.Background()
	create := `{"summary":"Unexplained waits","description":"details"}`

	t.Run("create requires a search first", func(t *testing.T) {
		s, _ := newTestSession(t, nil, &JiraState{})
		if _, err := s.createIssue(ctx, create); err == nil || !strings.Contains(err.Error(), "search") {
			t.Fatalf("err = %v", err)
		}
	})

	t.Run("create refused while an open issue exists", func(t *testing.T) {
		s, _ := newTestSession(t, []string{"PULP-5"}, &JiraState{})
		if _, err := s.search(ctx, "{}"); err != nil {
			t.Fatal(err)
		}
		if _, err := s.createIssue(ctx, create); err == nil || !strings.Contains(err.Error(), "PULP-5") {
			t.Fatalf("err = %v", err)
		}
	})

	t.Run("create sets project, type and labels; capped per run and per day", func(t *testing.T) {
		js := &JiraState{Creations: []time.Time{mustTime("2026-08-25T01:00:00Z")}}
		s, fj := newTestSession(t, nil, js)
		_, _ = s.search(ctx, "{}")
		jql := fj.bodies[0]["jql"].(string)
		if !strings.Contains(jql, `project = "PULP"`) || !strings.Contains(jql, `labels = "pulp-task-monitor-pulp-tasks-prod-unexplained-wait"`) || !strings.Contains(jql, "statusCategory != Done") {
			t.Errorf("jql = %s", jql)
		}
		if _, err := s.createIssue(ctx, `{"summary":"x","description":"y","project":"EVIL","labels":["x"]}`); err != nil {
			t.Fatal(err)
		}
		fields := fj.bodies[len(fj.bodies)-1]["fields"].(map[string]any)
		if fields["project"].(map[string]any)["key"] != "PULP" || fields["issuetype"].(map[string]any)["name"] != "Bug" {
			t.Errorf("fields = %v", fields)
		}
		labels, _ := json.Marshal(fields["labels"])
		if !strings.Contains(string(labels), "pulp-task-monitor-pulp-tasks-prod-unexplained-wait") {
			t.Errorf("labels = %s", labels)
		}
		if _, err := s.createIssue(ctx, create); err == nil {
			t.Error("second create in one run allowed")
		}
		if len(js.Creations) != 2 {
			t.Errorf("creations = %v", js.Creations)
		}
		// A new run with two creations in the last 24h hits the daily cap.
		s2, _ := newTestSession(t, nil, js)
		_, _ = s2.search(ctx, "{}")
		if _, err := s2.createIssue(ctx, create); err == nil || !strings.Contains(err.Error(), "cap") {
			t.Errorf("err = %v", err)
		}
	})

	t.Run("comments only on searched or tracked issues, capped, restricted", func(t *testing.T) {
		s, fj := newTestSession(t, []string{"PULP-5"}, &JiraState{IssueKey: "PULP-1"})
		if _, err := s.addComment(ctx, `{"issue_key":"PULP-5","body":"x"}`); err == nil {
			t.Error("comment before search allowed")
		}
		if _, err := s.getComments(ctx, `{"issue_key":"OTHER-1"}`); err == nil {
			t.Error("reading unrelated issue allowed")
		}
		_, _ = s.search(ctx, "{}")
		if _, err := s.addComment(ctx, `{"issue_key":"OTHER-7","body":"x"}`); err == nil {
			t.Error("comment on unrelated issue allowed")
		}
		if _, err := s.addComment(ctx, `{"issue_key":"PULP-5","body":"first"}`); err != nil {
			t.Fatal(err)
		}
		vis := fj.bodies[len(fj.bodies)-1]["visibility"].(map[string]any)
		if vis["type"] != "group" || vis["value"] != "Red Hat Employee" {
			t.Errorf("visibility = %v", vis)
		}
		if _, err := s.addComment(ctx, `{"issue_key":"PULP-1","body":"tracked"}`); err != nil {
			t.Fatal(err)
		}
		if _, err := s.addComment(ctx, `{"issue_key":"PULP-5","body":"third"}`); err == nil {
			t.Error("comment cap not enforced")
		}
		for _, r := range fj.requests {
			if strings.Contains(r, "transitions") || strings.HasPrefix(r, "DELETE") || strings.HasPrefix(r, "PUT") {
				t.Errorf("unexpected request %s", r)
			}
		}
	})
}

func TestRunJiraRecordsOutcome(t *testing.T) {
	ctx := context.Background()
	now := mustTime("2026-08-25T12:00:00Z")
	st := newState("pulp-tasks-prod", "https://packages.redhat.com")
	for i := range 6 {
		st.Events = append(st.Events, Event{Timestamp: now.Add(-time.Duration(30-5*i) * time.Minute), EventType: eventUnexplainedWait, TaskName: "sync", TaskHref: "/api/pulp/secret-domain/api/v3/tasks/abc/"})
	}
	fj := &fakeJira{}
	srv := httptest.NewServer(http.HandlerFunc(fj.handler))
	defer srv.Close()
	client := newJiraClient(srv.URL, "u", "t")

	// A model that searches and then creates.
	var prompt string
	creator := func(ctx context.Context, p string, tools []llms.Tool, mgr *mcpClient.MCPManager) (string, error) {
		prompt = p
		if _, err := mgr.CallTool(ctx, "search_incident_issues", "{}"); err != nil {
			return "", err
		}
		_, err := mgr.CallTool(ctx, "create_incident_issue", `{"summary":"s","description":"d"}`)
		return "created", err
	}
	if err := RunJira(ctx, testJiraCfg, client, creator, st, &SyncResult{}, now); err != nil {
		t.Fatal(err)
	}
	if st.Jira.IssueKey != "PULP-99" || st.Jira.IncidentStart == nil {
		t.Fatalf("jira state = %+v", st.Jira)
	}
	if strings.Contains(prompt, "secret-domain") || !strings.Contains(prompt, `"task_id": "abc"`) {
		t.Errorf("prompt leaks domain or misses task id:\n%s", prompt)
	}

	// A model that does nothing is an error.
	st.Jira.LastUpdate = ptr(now.Add(-2 * time.Hour))
	idle := func(context.Context, string, []llms.Tool, *mcpClient.MCPManager) (string, error) {
		return "nothing", nil
	}
	if err := RunJira(ctx, testJiraCfg, client, idle, st, &SyncResult{}, now); err == nil {
		t.Error("expected error when the model takes no action")
	}

	// Once the incident ends, a resolution comment clears tracking.
	later := now.Add(time.Hour)
	resolver := func(ctx context.Context, p string, tools []llms.Tool, mgr *mcpClient.MCPManager) (string, error) {
		_, err := mgr.CallTool(ctx, "add_incident_comment", `{"issue_key":"PULP-99","body":"resolved"}`)
		return "done", err
	}
	if err := RunJira(ctx, testJiraCfg, client, resolver, st, &SyncResult{}, later); err != nil {
		t.Fatal(err)
	}
	if st.Jira.IssueKey != "" || st.Jira.IncidentStart != nil {
		t.Errorf("tracking not cleared: %+v", st.Jira)
	}
}

func ptr[T any](v T) *T { return &v }

func TestTextToADF(t *testing.T) {
	doc := textToADF("line one\nline two\n\nsecond paragraph")
	content := doc["content"].([]any)
	if len(content) != 2 {
		t.Fatalf("paragraphs = %d", len(content))
	}
	if got := strings.TrimSpace(adfToText(roundTrip(doc))); got != "line one\nline two\nsecond paragraph" {
		t.Errorf("round trip = %q", got)
	}
}

func roundTrip(v any) any {
	b, _ := json.Marshal(v)
	var out any
	_ = json.Unmarshal(b, &out)
	return out
}

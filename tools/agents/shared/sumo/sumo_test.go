package sumo

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestNormalizeEndpoint(t *testing.T) {
	for in, want := range map[string]string{
		"https://api.sumologic.com":         "https://api.sumologic.com/api/v1",
		"https://api.us2.sumologic.com/":    "https://api.us2.sumologic.com/api/v1",
		"https://api.sumologic.com/api":     "https://api.sumologic.com/api/v1",
		"https://api.sumologic.com/api/v1/": "https://api.sumologic.com/api/v1",
	} {
		got, err := NormalizeEndpoint(in)
		if err != nil || got != want {
			t.Errorf("NormalizeEndpoint(%q) = %q, %v; want %q", in, got, err, want)
		}
	}
	if _, err := NormalizeEndpoint("api.sumologic.com"); err == nil {
		t.Error("expected error for endpoint without scheme")
	}
}

func TestParseTime(t *testing.T) {
	now := time.Date(2026, 1, 2, 12, 0, 0, 0, time.UTC)
	for in, want := range map[string]time.Time{
		"":                     now,
		"now":                  now,
		"-1h":                  now.Add(-time.Hour),
		"-30m":                 now.Add(-30 * time.Minute),
		"-2d":                  now.Add(-48 * time.Hour),
		"2026-01-02T10:00:00Z": now.Add(-2 * time.Hour),
	} {
		got, err := ParseTime(now, in)
		if err != nil || !got.Equal(want) {
			t.Errorf("ParseTime(%q) = %v, %v; want %v", in, got, err, want)
		}
	}
	for _, bad := range []string{"-1x", "yesterday", "-1h@h"} {
		if _, err := ParseTime(now, bad); err == nil {
			t.Errorf("ParseTime(%q): expected error", bad)
		}
	}
}

func TestFormatRowsDropsInternalFieldsAndRaw(t *testing.T) {
	out := FormatRows([]map[string]string{{
		"_raw": `{"reqpath":"api/pulp/x"}`, "_collector": "c", "_messagetime": "1767355200000",
		"reqpath": "api/pulp/x", "statuscode": "503",
	}})
	if strings.Contains(out, "_raw") || strings.Contains(out, "_collector") {
		t.Errorf("internal fields not dropped: %s", out)
	}
	if !strings.Contains(out, `"_messagetime":"2026-01-02T12:00:00Z"`) || !strings.Contains(out, `"statuscode":"503"`) {
		t.Errorf("unexpected output: %s", out)
	}
	// Without extracted fields, _raw is the only content and must be kept.
	if out := FormatRows([]map[string]string{{"_raw": "line"}}); !strings.Contains(out, `"_raw":"line"`) {
		t.Errorf("_raw dropped without extracted fields: %s", out)
	}
}

// fakeSumo emulates the Search Job API, including the session-cookie
// requirement for follow-up requests.
func fakeSumo(t *testing.T, status jobStatus, rows string) (*httptest.Server, *[]string) {
	t.Helper()
	var calls []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls = append(calls, r.Method+" "+r.URL.Path)
		if id, key, ok := r.BasicAuth(); !ok || id != "id" || key != "key" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		if r.Method == http.MethodPost && r.URL.Path == "/api/v1/search/jobs" {
			var body createJobRequest
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Query == "" || body.From >= body.To {
				t.Errorf("bad create body: %+v %v", body, err)
			}
			http.SetCookie(w, &http.Cookie{Name: "JSESSIONID", Value: "s1", Path: "/"})
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(`{"id":"J1"}`))
			return
		}
		if c, err := r.Cookie("JSESSIONID"); err != nil || c.Value != "s1" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		switch {
		case r.Method == http.MethodDelete:
			w.WriteHeader(http.StatusOK)
		case r.URL.Path == "/api/v1/search/jobs/J1":
			_ = json.NewEncoder(w).Encode(status)
		case strings.HasPrefix(r.URL.Path, "/api/v1/search/jobs/J1/"):
			calls = append(calls, "limit="+r.URL.Query().Get("limit"))
			_, _ = w.Write([]byte(rows))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)
	return srv, &calls
}

func TestHandleMessages(t *testing.T) {
	srv, calls := fakeSumo(t, jobStatus{State: stateDone, MessageCount: 1},
		`{"messages":[{"map":{"reqpath":"api/pulp/x","statuscode":"504","_sourcehost":"h"}}]}`)
	c, err := NewClient(srv.URL, "id", "key")
	if err != nil {
		t.Fatal(err)
	}
	out, err := c.Handle(context.Background(), `{"query":"_index=rh_akamai | json auto","earliest_time":"-30m"}`)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "result_type=messages total=1 returned=1") || !strings.Contains(out, `"statuscode":"504"`) {
		t.Errorf("unexpected output:\n%s", out)
	}
	joined := strings.Join(*calls, ",")
	if !strings.Contains(joined, "GET /api/v1/search/jobs/J1/messages") || !strings.HasSuffix(joined, "DELETE /api/v1/search/jobs/J1") {
		t.Errorf("unexpected call sequence: %s", joined)
	}
}

func TestHandleRecordsPreferredForAggregates(t *testing.T) {
	srv, calls := fakeSumo(t, jobStatus{State: stateDone, MessageCount: 50, RecordCount: 2},
		`{"records":[{"map":{"statuscode":"503","_count":"40"}},{"map":{"statuscode":"504","_count":"10"}}]}`)
	c, _ := NewClient(srv.URL, "id", "key")
	out, err := c.Handle(context.Background(), `{"query":"_index=rh_akamai | count by statuscode"}`)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "result_type=records total=2") || !strings.Contains(strings.Join(*calls, ","), "/records") {
		t.Errorf("records not used:\n%s\n%v", out, *calls)
	}
}

func TestHandleSurfacesJobErrors(t *testing.T) {
	srv, _ := fakeSumo(t, jobStatus{State: "GATHERING RESULTS", PendingErrors: []string{"Parse error"}}, `{}`)
	c, _ := NewClient(srv.URL, "id", "key")
	if _, err := c.Handle(context.Background(), `{"query":"bad |"}`); err == nil || !strings.Contains(err.Error(), "Parse error") {
		t.Errorf("expected parse error, got %v", err)
	}

	c, _ = NewClient(srv.URL, "wrong", "key")
	if _, err := c.Handle(context.Background(), `{"query":"x"}`); err == nil || !strings.Contains(err.Error(), "401") {
		t.Errorf("expected 401, got %v", err)
	}
}

func TestHandleValidatesWindow(t *testing.T) {
	c, _ := NewClient("https://api.sumologic.com", "id", "key")
	for _, args := range []string{
		`{"query":"x","earliest_time":"-8d"}`,
		`{"query":"x","earliest_time":"now","latest_time":"-1h"}`,
		`{"query":""}`,
	} {
		if _, err := c.Handle(context.Background(), args); err == nil {
			t.Errorf("%s: expected error", args)
		}
	}
}

func TestNewClientFromEnv(t *testing.T) {
	t.Setenv(EnvAPIURL, "")
	if _, err := NewClientFromEnv(); err != ErrNotConfigured {
		t.Errorf("unset URL: got %v, want ErrNotConfigured", err)
	}

	t.Setenv(EnvAPIURL, "https://api.sumologic.com")
	t.Setenv(EnvAccessID, "id")
	t.Setenv(EnvAccessKey, "")
	if _, err := NewClientFromEnv(); err == nil || err == ErrNotConfigured {
		t.Errorf("missing key: got %v, want credentials error", err)
	}

	t.Setenv(EnvAccessKey, "key")
	c, err := NewClientFromEnv()
	if err != nil || c.Endpoint() != "https://api.sumologic.com/api/v1" {
		t.Errorf("got %v, %v", c, err)
	}
}

func TestSearchRowsReturnsRawRecords(t *testing.T) {
	srv, calls := fakeSumo(t, jobStatus{State: stateDone, MessageCount: 50, RecordCount: 3},
		`{"records":[{"map":{"reqhost":"packages.redhat.com","_count":"40"}},{"map":{"reqhost":"console.redhat.com","_count":"10"}}]}`)
	c, _ := NewClient(srv.URL, "id", "key")
	c.SetJobTimeout(time.Minute)
	now := time.Now()
	res, err := c.SearchRows(context.Background(), "_index=rh_akamai | count by reqhost", now.Add(-time.Hour), now, 2)
	if err != nil {
		t.Fatal(err)
	}
	if res.Kind != "records" || res.Total != 3 || len(res.Rows) != 2 || res.Rows[0]["_count"] != "40" {
		t.Errorf("unexpected result: %+v", res)
	}
	if !strings.Contains(strings.Join(*calls, ","), "limit=2") {
		t.Errorf("maxResults not applied to the fetch: %v", *calls)
	}
}

func TestNewClientFromEnvRequiresHTTPS(t *testing.T) {
	t.Setenv(EnvAPIURL, "http://api.sumologic.com")
	t.Setenv(EnvAccessID, "id")
	t.Setenv(EnvAccessKey, "key")
	if _, err := NewClientFromEnv(); err == nil || !strings.Contains(err.Error(), "https") {
		t.Errorf("plain HTTP endpoint accepted: %v", err)
	}
}

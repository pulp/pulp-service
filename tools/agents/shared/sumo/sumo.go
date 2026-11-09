// Package sumo exposes a Sumo Logic search tool (sumo_search) for agents.
// It is used, for example, to read the Akamai DataStream access logs
// (_index=rh_akamai).
//
// Configuration comes from the environment (see NewClientFromEnv):
//
//	SUMO_API_URL     deployment API endpoint, e.g. https://api.sumologic.com
//	SUMO_ACCESS_ID   access ID
//	SUMO_ACCESS_KEY  access key
//
// Agents using shared/mcp-client call RegisterTool; agents with their own tool
// manager register Tool() with Handle as the handler.
package sumo

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/tmc/langchaingo/llms"

	mcpClient "github.com/pulp/pulp-service/tools/agents/shared/mcp-client"
	"github.com/pulp/pulp-service/tools/agents/shared/utils"
)

// Environment variables read by NewClientFromEnv.
const (
	EnvAPIURL    = "SUMO_API_URL"
	EnvAccessID  = "SUMO_ACCESS_ID"
	EnvAccessKey = "SUMO_ACCESS_KEY"
)

// ErrNotConfigured is returned by NewClientFromEnv when SUMO_API_URL is unset.
var ErrNotConfigured = errors.New(EnvAPIURL + " is not set")

// SampleQuery is the reference query for pulp 5xx errors at the Akamai edge.
const SampleQuery = `_index=rh_akamai "packages.redhat.com" | json auto | where reqhost = "packages.redhat.com" and num(statuscode) >= 500 and reqpath matches "*api/pulp*"`

const (
	defaultMaxResults = 100
	maxMaxResults     = 1000
	maxWindow         = 7 * 24 * time.Hour
	maxOutputBytes    = 28000
	maxPageSize       = 10000 // Search Job API limit per messages/records request
	defaultJobTimeout = 4 * time.Minute
	pollInterval      = 2 * time.Second
)

// Search job states returned by the Sumo Logic Search Job API.
const (
	stateDone        = "DONE GATHERING RESULTS"
	stateCancelled   = "CANCELLED"
	stateForcePaused = "FORCE PAUSED"
)

// internalFields are Sumo metadata fields that carry no value for the analysis
// and only consume tokens.
var internalFields = map[string]bool{
	"_blockid": true, "_collector": true, "_collectorid": true, "_format": true,
	"_messagecount": true, "_messageid": true, "_receipttime": true, "_size": true,
	"_source": true, "_sourcecategory": true, "_sourcehost": true, "_sourceid": true,
	"_sourcename": true, "_view": true, "_dte": true,
}

// Client talks to the Sumo Logic Search Job API.
type Client struct {
	baseURL    string // e.g. https://api.sumologic.com/api/v1
	accessID   string
	accessKey  string
	httpClient *http.Client
	now        func() time.Time
	jobTimeout time.Duration
}

// NewClientFromEnv builds a client from SUMO_API_URL, SUMO_ACCESS_ID and
// SUMO_ACCESS_KEY. It returns ErrNotConfigured when SUMO_API_URL is unset, so
// callers can treat Sumo as optional.
func NewClientFromEnv() (*Client, error) {
	endpoint := os.Getenv(EnvAPIURL)
	if endpoint == "" {
		return nil, ErrNotConfigured
	}
	accessID, accessKey := os.Getenv(EnvAccessID), os.Getenv(EnvAccessKey)
	if accessID == "" || accessKey == "" {
		return nil, fmt.Errorf("%s and %s are required when %s is set", EnvAccessID, EnvAccessKey, EnvAPIURL)
	}
	// The access ID/key are sent as Basic Auth on every request: never over plain HTTP.
	if u, err := url.Parse(strings.TrimSpace(endpoint)); err != nil || u.Scheme != "https" {
		return nil, fmt.Errorf("%s must be an https:// URL, got %q", EnvAPIURL, endpoint)
	}
	return NewClient(endpoint, accessID, accessKey)
}

// Endpoint returns the API base URL the client talks to.
func (c *Client) Endpoint() string { return c.baseURL }

// NewClient builds a client. endpoint is the deployment API endpoint, e.g.
// https://api.sumologic.com or https://api.us2.sumologic.com (an /api or
// /api/v1 suffix is accepted).
func NewClient(endpoint, accessID, accessKey string) (*Client, error) {
	base, err := NormalizeEndpoint(endpoint)
	if err != nil {
		return nil, err
	}
	// The Search Job API requires the session cookies returned on job creation
	// to be sent on every follow-up request, otherwise it returns 404.
	jar, err := cookiejar.New(nil)
	if err != nil {
		return nil, err
	}
	return &Client{
		baseURL:    base,
		accessID:   accessID,
		accessKey:  accessKey,
		httpClient: &http.Client{Timeout: time.Minute, Jar: jar},
		now:        time.Now,
		jobTimeout: defaultJobTimeout,
	}, nil
}

// SetJobTimeout sets how long Search/SearchRows wait for a search job to
// finish (default 4 minutes). Long aggregations, e.g. over 7 days of Akamai
// logs, need more.
func (c *Client) SetJobTimeout(d time.Duration) {
	if d > 0 {
		c.jobTimeout = d
	}
}

// NormalizeEndpoint returns the API v1 base URL for a Sumo deployment endpoint.
func NormalizeEndpoint(endpoint string) (string, error) {
	u, err := url.Parse(strings.TrimSpace(endpoint))
	if err != nil || u.Scheme == "" || u.Host == "" {
		return "", fmt.Errorf("invalid %s %q: expected e.g. https://api.sumologic.com", EnvAPIURL, endpoint)
	}
	p := strings.TrimRight(u.Path, "/")
	p = strings.TrimSuffix(p, "/v1")
	p = strings.TrimSuffix(p, "/api")
	u.Path = p + "/api/v1"
	return u.String(), nil
}

// ParseTime converts a time argument into an absolute time. Accepted forms:
// "now", relative durations like "-1h", "-30m", "-2d", and RFC3339 timestamps.
func ParseTime(now time.Time, s string) (time.Time, error) {
	s = strings.TrimSpace(s)
	if s == "" || s == "now" {
		return now.UTC(), nil
	}
	if strings.HasPrefix(s, "-") {
		spec := s[1:]
		if daysStr, ok := strings.CutSuffix(spec, "d"); ok {
			days, err := strconv.Atoi(daysStr)
			if err != nil {
				return time.Time{}, fmt.Errorf("invalid relative time %q", s)
			}
			return now.UTC().Add(-time.Duration(days) * 24 * time.Hour), nil
		}
		d, err := time.ParseDuration(spec)
		if err != nil {
			return time.Time{}, fmt.Errorf("invalid relative time %q (use e.g. -1h, -30m, -2d)", s)
		}
		return now.UTC().Add(-d), nil
	}
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02T15:04:05", "2006-01-02 15:04:05"} {
		if t, err := time.Parse(layout, s); err == nil {
			return t.UTC(), nil
		}
	}
	return time.Time{}, fmt.Errorf("invalid time %q: use now, -1h, -30m, -2d, or RFC3339 (2026-01-02T15:04:05Z)", s)
}

type createJobRequest struct {
	Query         string `json:"query"`
	From          int64  `json:"from"`
	To            int64  `json:"to"`
	TimeZone      string `json:"timeZone"`
	ByReceiptTime bool   `json:"byReceiptTime"`
}

type jobStatus struct {
	State           string   `json:"state"`
	MessageCount    int      `json:"messageCount"`
	RecordCount     int      `json:"recordCount"`
	PendingErrors   []string `json:"pendingErrors"`
	PendingWarnings []string `json:"pendingWarnings"`
}

type resultRows struct {
	Messages []struct {
		Map map[string]string `json:"map"`
	} `json:"messages"`
	Records []struct {
		Map map[string]string `json:"map"`
	} `json:"records"`
}

func (c *Client) do(ctx context.Context, method, path string, body any) ([]byte, error) {
	var reader io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		reader = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, reader)
	if err != nil {
		return nil, err
	}
	req.SetBasicAuth(c.accessID, c.accessKey)
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("%s %s: status %d: %s", method, path, resp.StatusCode, utils.Truncate(string(data), 500))
	}
	return data, nil
}

// Result holds the rows returned by SearchRows.
type Result struct {
	// Kind is "records" for aggregate queries, otherwise "messages".
	Kind string
	// Total is the number of records/messages produced by the job; Rows may
	// hold fewer when capped by maxResults.
	Total    int
	Rows     []map[string]string
	Warnings []string
}

// SearchRows runs a query over [from, to) and returns the raw result rows: the
// aggregate records when the query produces them, otherwise the messages.
func (c *Client) SearchRows(ctx context.Context, query string, from, to time.Time, maxResults int) (Result, error) {
	data, err := c.do(ctx, http.MethodPost, "/search/jobs", createJobRequest{
		Query: query, From: from.UnixMilli(), To: to.UnixMilli(), TimeZone: "UTC",
	})
	if err != nil {
		return Result{}, fmt.Errorf("create search job: %w", err)
	}
	var job struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(data, &job); err != nil || job.ID == "" {
		return Result{}, fmt.Errorf("create search job: unexpected response: %s", utils.Truncate(string(data), 500))
	}
	jobPath := "/search/jobs/" + url.PathEscape(job.ID)
	defer func() {
		// Free the job on the Sumo side; ignore errors (jobs also expire on their own).
		cleanupCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_, _ = c.do(cleanupCtx, http.MethodDelete, jobPath, nil)
	}()
	fmt.Fprintf(os.Stderr, "[sumo] job %s created\n", job.ID)

	status, err := c.waitForJob(ctx, jobPath)
	if err != nil {
		return Result{}, err
	}

	res := Result{Kind: "messages", Total: status.MessageCount, Warnings: status.PendingWarnings}
	if status.RecordCount > 0 {
		res.Kind, res.Total = "records", status.RecordCount
	}
	limit := min(res.Total, maxResults)
	for offset := 0; offset < limit; {
		page := min(limit-offset, maxPageSize)
		data, err = c.do(ctx, http.MethodGet, fmt.Sprintf("%s/%s?offset=%d&limit=%d", jobPath, res.Kind, offset, page), nil)
		if err != nil {
			return Result{}, fmt.Errorf("fetch %s: %w", res.Kind, err)
		}
		var rows resultRows
		if err := json.Unmarshal(data, &rows); err != nil {
			return Result{}, fmt.Errorf("parse %s: %w", res.Kind, err)
		}
		n := len(rows.Messages) + len(rows.Records)
		for _, m := range rows.Messages {
			res.Rows = append(res.Rows, m.Map)
		}
		for _, r := range rows.Records {
			res.Rows = append(res.Rows, r.Map)
		}
		if n == 0 {
			break
		}
		offset += n
	}
	return res, nil
}

// Search runs a query over [from, to) and returns the aggregate records when the
// query produces them, otherwise the raw messages, formatted for an LLM.
func (c *Client) Search(ctx context.Context, query string, from, to time.Time, maxResults int) (string, error) {
	res, err := c.SearchRows(ctx, query, from, to, maxResults)
	if err != nil {
		return "", err
	}
	var sb strings.Builder
	fmt.Fprintf(&sb, "window=%s..%s result_type=%s total=%d returned=%d\n",
		from.Format(time.RFC3339), to.Format(time.RFC3339), res.Kind, res.Total, len(res.Rows))
	if len(res.Warnings) > 0 {
		fmt.Fprintf(&sb, "warnings: %s\n", strings.Join(res.Warnings, "; "))
	}
	sb.WriteString(FormatRows(res.Rows))
	return sb.String(), nil
}

func (c *Client) waitForJob(ctx context.Context, jobPath string) (jobStatus, error) {
	deadline := time.Now().Add(c.jobTimeout)
	for {
		data, err := c.do(ctx, http.MethodGet, jobPath, nil)
		if err != nil {
			return jobStatus{}, fmt.Errorf("search job status: %w", err)
		}
		var st jobStatus
		if err := json.Unmarshal(data, &st); err != nil {
			return jobStatus{}, fmt.Errorf("parse search job status: %w", err)
		}
		if len(st.PendingErrors) > 0 {
			return st, fmt.Errorf("search job failed: %s", strings.Join(st.PendingErrors, "; "))
		}
		switch st.State {
		case stateDone:
			return st, nil
		case stateCancelled, stateForcePaused:
			return st, fmt.Errorf("search job %s", strings.ToLower(st.State))
		}
		if time.Now().After(deadline) {
			return st, fmt.Errorf("search job did not finish within %s (state %q); narrow the time range", c.jobTimeout, st.State)
		}
		select {
		case <-ctx.Done():
			return st, ctx.Err()
		case <-time.After(pollInterval):
		}
	}
}

// FormatRows renders rows as one JSON object per line with Sumo internal
// fields removed. _raw is dropped when the query already extracted fields
// (e.g. via "json auto"), since it duplicates them.
func FormatRows(rows []map[string]string) string {
	var sb strings.Builder
	for _, row := range rows {
		clean := make(map[string]string, len(row))
		extracted := 0
		for k, v := range row {
			if internalFields[strings.ToLower(k)] {
				continue
			}
			if !strings.HasPrefix(k, "_") {
				extracted++
			}
			clean[k] = v
		}
		if extracted > 0 {
			delete(clean, "_raw")
		}
		if t, ok := clean["_messagetime"]; ok {
			if ms, err := strconv.ParseInt(t, 10, 64); err == nil {
				clean["_messagetime"] = time.UnixMilli(ms).UTC().Format(time.RFC3339Nano)
			}
		}
		b, _ := json.Marshal(sortedMap(clean))
		sb.Write(b)
		sb.WriteString("\n")
	}
	return sb.String()
}

// sortedMap returns an ordered representation so output is stable.
func sortedMap(m map[string]string) json.RawMessage {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var buf bytes.Buffer
	buf.WriteByte('{')
	for i, k := range keys {
		if i > 0 {
			buf.WriteByte(',')
		}
		kb, _ := json.Marshal(k)
		vb, _ := json.Marshal(m[k])
		buf.Write(kb)
		buf.WriteByte(':')
		buf.Write(vb)
	}
	buf.WriteByte('}')
	return buf.Bytes()
}

type toolArgs struct {
	Query        string `json:"query"`
	EarliestTime string `json:"earliest_time"`
	LatestTime   string `json:"latest_time"`
	MaxResults   int    `json:"max_results"`
}

// Handle executes one sumo_search tool call.
func (c *Client) Handle(ctx context.Context, argsJSON string) (string, error) {
	var args toolArgs
	if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
		return "", fmt.Errorf("parse sumo_search arguments: %w", err)
	}
	if strings.TrimSpace(args.Query) == "" {
		return "", fmt.Errorf("query is required")
	}
	if args.EarliestTime == "" {
		args.EarliestTime = "-1h"
	}
	now := c.now()
	from, err := ParseTime(now, args.EarliestTime)
	if err != nil {
		return "", fmt.Errorf("earliest_time: %w", err)
	}
	to, err := ParseTime(now, args.LatestTime)
	if err != nil {
		return "", fmt.Errorf("latest_time: %w", err)
	}
	if !from.Before(to) {
		return "", fmt.Errorf("earliest_time (%s) must be before latest_time (%s)", from.Format(time.RFC3339), to.Format(time.RFC3339))
	}
	if to.Sub(from) > maxWindow {
		return "", fmt.Errorf("time range %s exceeds the maximum of %s", to.Sub(from), maxWindow)
	}
	maxResults := args.MaxResults
	if maxResults <= 0 {
		maxResults = defaultMaxResults
	}
	maxResults = min(maxResults, maxMaxResults)

	fmt.Fprintf(os.Stderr, "[sumo] query: %s\n[sumo] window: %s..%s max_results=%d\n",
		args.Query, from.Format(time.RFC3339), to.Format(time.RFC3339), maxResults)
	out, err := c.Search(ctx, args.Query, from, to, maxResults)
	if err != nil {
		return "", err
	}
	fmt.Fprintf(os.Stderr, "[sumo] results: %d bytes\n", len(out))
	return utils.Truncate(out, maxOutputBytes), nil
}

// RegisterTool registers the sumo_search tool on a shared/mcp-client manager
// and returns its definition.
func (c *Client) RegisterTool(mgr *mcpClient.MCPManager) llms.Tool {
	tool := c.Tool()
	mgr.RegisterNativeTool(tool, c.Handle)
	return tool
}

// Tool returns the sumo_search tool definition. Register it with Handle as the
// handler.
func (c *Client) Tool() llms.Tool {
	return llms.Tool{
		Type: "function",
		Function: &llms.FunctionDefinition{
			Name: "sumo_search",
			Description: "Run a Sumo Logic search query and return the results. Use it to read the Akamai edge " +
				"access logs (_index=rh_akamai). Log lines are JSON: add '| json auto' to extract fields. " +
				"Field names are lowercase (reqhost, reqpath, statuscode, ...). statuscode is a string: compare with num(statuscode). " +
				"Aggregate queries (count by ...) return records; other queries return messages. Example: " + SampleQuery,
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"query": map[string]any{
						"type":        "string",
						"description": "Sumo Logic search query. Example: " + SampleQuery,
					},
					"earliest_time": map[string]any{
						"type":        "string",
						"description": "Start of the range: relative (-1h, -30m, -2d) or RFC3339 UTC (2026-01-02T15:04:05Z). Default: -1h",
					},
					"latest_time": map[string]any{
						"type":        "string",
						"description": "End of the range: now, relative (-10m), or RFC3339 UTC. Default: now",
					},
					"max_results": map[string]any{
						"type":        "integer",
						"description": fmt.Sprintf("Maximum number of messages/records to return. Default: %d, max: %d", defaultMaxResults, maxMaxResults),
					},
				},
				"required": []string{"query"},
			},
		},
	}
}

package splunk

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/tmc/langchaingo/llms"

	mcpClient "agent-splunk/mcp-client"
)

const SPLUNK_SAMPLE_QUERY = "index=federated:rh_akamai sourcetype=datastream:access statusCode>=500 reqHost=packages.redhat.com"

// Client handles communication with the Splunk REST API.
type Client struct {
	BaseURL    string
	Token      string
	HTTPClient *http.Client
}

// NewClient creates a Splunk client from the given base URL and bearer token.
// Set the SPLUNK_INSECURE_SKIP_VERIFY environment variable to "true" to skip
// TLS certificate verification (e.g. when the Splunk API uses an internal CA).
func NewClient(baseURL, token string) *Client {
	skipVerify := strings.EqualFold(os.Getenv("SPLUNK_INSECURE_SKIP_VERIFY"), "true")
	return &Client{
		BaseURL: strings.TrimRight(baseURL, "/"),
		Token:   token,
		HTTPClient: &http.Client{
			Timeout: 5 * time.Minute,
			Transport: &http.Transport{
				TLSClientConfig: &tls.Config{InsecureSkipVerify: skipVerify},
			},
		},
	}
}

// searchJobResponse is the JSON returned when creating a search job.
type searchJobResponse struct {
	SID string `json:"sid"`
}

// jobStatusEntry represents one entry in the Splunk job status response.
type jobStatusEntry struct {
	Content struct {
		DispatchState string  `json:"dispatchState"`
		DoneProgress  float64 `json:"doneProgress"`
		ResultCount   int     `json:"resultCount"`
	} `json:"content"`
}

// SearchResults holds the structured results from a Splunk search.
type SearchResults struct {
	Results []map[string]any `json:"results"`
}

// RegisterTool registers the splunk_search tool on the given MCPManager and
// returns the langchaingo tool definition.
func (c *Client) RegisterTool(mgr *mcpClient.MCPManager) llms.Tool {
	tool := llms.Tool{
		Type: "function",
		Function: &llms.FunctionDefinition{
			Name:        "splunk_search",
			Description: "Run a Splunk search query (SPL) and return the results. Use this tool to search logs, events, and metrics in Splunk. Example query: " + SPLUNK_SAMPLE_QUERY,
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"query": map[string]any{
						"type":        "string",
						"description": "The Splunk search query (SPL). For example: " + SPLUNK_SAMPLE_QUERY,
					},
					"earliest_time": map[string]any{
						"type":        "string",
						"description": "The earliest time for the search range. Splunk time modifiers like -24h, -7d, -1h@h, or absolute times like 2024-01-01T00:00:00. Default: -24h",
					},
					"latest_time": map[string]any{
						"type":        "string",
						"description": "The latest time for the search range. Splunk time modifiers like now, -1h, or absolute times. Default: now",
					},
					"max_results": map[string]any{
						"type":        "integer",
						"description": "Maximum number of results to return. Default: 100",
					},
				},
				"required": []string{"query"},
			},
		},
	}

	mgr.RegisterNativeTool(tool, func(ctx context.Context, argsJSON string) (string, error) {
		var args struct {
			Query        string `json:"query"`
			EarliestTime string `json:"earliest_time"`
			LatestTime   string `json:"latest_time"`
			MaxResults   int    `json:"max_results"`
		}
		if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
			return "", fmt.Errorf("parse splunk_search arguments: %w", err)
		}

		if args.EarliestTime == "" {
			args.EarliestTime = "-24h"
		}
		if args.LatestTime == "" {
			args.LatestTime = "now"
		}

		return c.Search(ctx, args.Query, args.EarliestTime, args.LatestTime, args.MaxResults)
	})

	return tool
}

// Search runs a Splunk search query (SPL) and returns the results as JSON.
// It creates a search job, polls until completion, and fetches the results.
func (c *Client) Search(ctx context.Context, query string, earliest, latest string, maxResults int) (string, error) {
	if maxResults <= 0 {
		maxResults = 100
	}

	// Ensure query starts with "search" command if it doesn't already.
	trimmed := strings.TrimSpace(query)
	if !strings.HasPrefix(trimmed, "|") && !strings.HasPrefix(strings.ToLower(trimmed), "search ") {
		query = "search " + trimmed
	}

	fmt.Fprintf(os.Stderr, "[splunk-debug] SPL query: %s\n", query)
	fmt.Fprintf(os.Stderr, "[splunk-debug] earliest_time: %s, latest_time: %s, max_results: %d\n", earliest, latest, maxResults)

	// 1. Create the search job.
	sid, err := c.createJob(ctx, query, earliest, latest)
	if err != nil {
		return "", fmt.Errorf("create search job: %w", err)
	}
	fmt.Fprintf(os.Stderr, "[splunk-debug] job created, SID: %s\n", sid)

	// 2. Poll until the job is done.
	if err := c.waitForJob(ctx, sid); err != nil {
		return "", fmt.Errorf("wait for search job: %w", err)
	}
	fmt.Fprintf(os.Stderr, "[splunk-debug] job completed\n")

	// 3. Fetch results.
	results, err := c.fetchResults(ctx, sid, maxResults)
	if err != nil {
		return "", fmt.Errorf("fetch results: %w", err)
	}

	fmt.Fprintf(os.Stderr, "[splunk-debug] results length: %d bytes\n", len(results))
	fmt.Fprintf(os.Stderr, "[splunk-debug] raw results: %s\n", results)

	return results, nil
}

func (c *Client) createJob(ctx context.Context, query, earliest, latest string) (string, error) {
	data := url.Values{}
	data.Set("search", query)
	data.Set("output_mode", "json")
	if earliest != "" {
		data.Set("earliest_time", earliest)
	}
	if latest != "" {
		data.Set("latest_time", latest)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		c.BaseURL+"/services/search/jobs", strings.NewReader(data.Encode()))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Authorization", "Bearer "+c.Token)

	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusCreated && resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("unexpected status %d: %s", resp.StatusCode, string(body))
	}

	var job searchJobResponse
	if err := json.Unmarshal(body, &job); err != nil {
		return "", fmt.Errorf("parse job response: %w (body: %s)", err, string(body))
	}
	if job.SID == "" {
		return "", fmt.Errorf("empty SID in response: %s", string(body))
	}
	return job.SID, nil
}

func (c *Client) waitForJob(ctx context.Context, sid string) error {
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			done, err := c.isJobDone(ctx, sid)
			if err != nil {
				return err
			}
			if done {
				return nil
			}
		}
	}
}

func (c *Client) isJobDone(ctx context.Context, sid string) (bool, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet,
		c.BaseURL+"/services/search/jobs/"+url.PathEscape(sid)+"?output_mode=json", nil)
	if err != nil {
		return false, err
	}
	req.Header.Set("Authorization", "Bearer "+c.Token)

	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		return false, fmt.Errorf("job status %d: %s", resp.StatusCode, string(body))
	}

	var entries []jobStatusEntry
	if err := json.Unmarshal(body, &entries); err != nil {
		// Try alternate format: {"entry": [...]}
		var wrapper struct {
			Entry []jobStatusEntry `json:"entry"`
		}
		if err2 := json.Unmarshal(body, &wrapper); err2 != nil {
			return false, fmt.Errorf("parse job status: %w (body: %s)", err, string(body))
		}
		entries = wrapper.Entry
	}

	if len(entries) == 0 {
		return false, fmt.Errorf("no entries in job status response")
	}

	state := entries[0].Content.DispatchState
	fmt.Fprintf(os.Stderr, "[splunk-debug] job %s state: %s, progress: %.1f%%, resultCount: %d\n",
		sid, state, entries[0].Content.DoneProgress*100, entries[0].Content.ResultCount)
	return state == "DONE", nil
}

func (c *Client) fetchResults(ctx context.Context, sid string, maxResults int) (string, error) {
	u := fmt.Sprintf("%s/services/search/jobs/%s/results?output_mode=json&count=%d",
		c.BaseURL, url.PathEscape(sid), maxResults)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("Authorization", "Bearer "+c.Token)

	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("fetch results status %d: %s", resp.StatusCode, string(body))
	}

	return string(body), nil
}

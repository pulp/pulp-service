package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// PulpAPI is what the sync needs from Pulp. It is an interface so the sync
// logic can be tested without a server.
type PulpAPI interface {
	FinishedTasks(ctx context.Context, start, end time.Time) ([]Task, error)
	TasksInState(ctx context.Context, state string) ([]Task, error)
	OnlineWorkers(ctx context.Context) (int, error)
	// ResolveNames maps PRNs to display names. It is best effort: PRNs it
	// cannot resolve are left out of the result.
	ResolveNames(ctx context.Context, prns []string) map[string]string
}

const (
	taskPageSize = 500
	// finishedChunk bounds each finished-task query, as the swamp backfill did,
	// so long gaps do not turn into one huge paginated query.
	finishedChunk = 3 * time.Hour
	maxPages      = 2000
	nameBatchSize = 50
)

// retryBackoff is the base delay between retries of transient failures.
var retryBackoff = 2 * time.Second

// taskFields limits the tasks API response to what the monitor uses.
var taskFields = "pulp_href,state,name,pulp_created,started_at,finished_at,reserved_resources_record"

// PulpClient talks to the Pulp admin tasks API with basic auth.
type PulpClient struct {
	BaseURL     string
	TaskAPIPath string
	StatusPath  string
	Username    string
	Password    string
	HTTP        *http.Client
}

func newPulpClient(baseURL, taskAPIPath, statusPath, username, password string) *PulpClient {
	return &PulpClient{
		BaseURL:     strings.TrimRight(baseURL, "/"),
		TaskAPIPath: taskAPIPath,
		StatusPath:  statusPath,
		Username:    username,
		Password:    password,
		HTTP:        &http.Client{Timeout: 60 * time.Second},
	}
}

// apiError is a non-2xx response. 4xx errors other than 429 are not retried.
type apiError struct {
	Status int
	Body   string
	URL    string
}

func (e *apiError) Error() string {
	return fmt.Sprintf("Pulp API %d for %s: %s", e.Status, e.URL, truncate(e.Body, 500))
}

func (c *PulpClient) getJSON(ctx context.Context, rawURL string, out any) error {
	var lastErr error
	for attempt := range 3 {
		if attempt > 0 {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(time.Duration(attempt) * retryBackoff):
			}
		}
		lastErr = c.getJSONOnce(ctx, rawURL, out)
		if lastErr == nil {
			return nil
		}
		var apiErr *apiError
		if errors.As(lastErr, &apiErr) && apiErr.Status < 500 && apiErr.Status != http.StatusTooManyRequests {
			return lastErr
		}
		if ctx.Err() != nil {
			return lastErr
		}
		fmt.Fprintf(os.Stderr, "[pulp] request failed (attempt %d/3): %v\n", attempt+1, lastErr)
	}
	return lastErr
}

func (c *PulpClient) getJSONOnce(ctx context.Context, rawURL string, out any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return err
	}
	req.SetBasicAuth(c.Username, c.Password)
	req.Header.Set("Accept", "application/json")
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("read response from %s: %w", rawURL, err)
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return &apiError{Status: resp.StatusCode, Body: string(body), URL: redactQuery(rawURL)}
	}
	if err := json.Unmarshal(body, out); err != nil {
		return fmt.Errorf("decode response from %s: %w", redactQuery(rawURL), err)
	}
	return nil
}

func redactQuery(rawURL string) string {
	base, _, _ := strings.Cut(rawURL, "?")
	return base
}

type rawTask struct {
	PulpHref                string  `json:"pulp_href"`
	State                   string  `json:"state"`
	Name                    string  `json:"name"`
	PulpCreated             string  `json:"pulp_created"`
	StartedAt               *string `json:"started_at"`
	FinishedAt              *string `json:"finished_at"`
	ReservedResourcesRecord []any   `json:"reserved_resources_record"`
}

func (r rawTask) toTask() (Task, error) {
	t := Task{Href: r.PulpHref, State: r.State, Name: r.Name}
	created, err := time.Parse(time.RFC3339Nano, r.PulpCreated)
	if err != nil {
		return t, fmt.Errorf("task %s: bad pulp_created %q: %w", r.PulpHref, r.PulpCreated, err)
	}
	t.Created = created
	if t.StartedAt, err = parseOptionalTime(r.StartedAt); err != nil {
		return t, fmt.Errorf("task %s: bad started_at: %w", r.PulpHref, err)
	}
	if t.FinishedAt, err = parseOptionalTime(r.FinishedAt); err != nil {
		return t, fmt.Errorf("task %s: bad finished_at: %w", r.PulpHref, err)
	}
	for _, v := range r.ReservedResourcesRecord {
		s, ok := v.(string)
		if !ok {
			continue
		}
		if shared, ok := strings.CutPrefix(s, "shared:"); ok {
			t.Shared = append(t.Shared, shared)
		} else {
			t.Exclusive = append(t.Exclusive, s)
		}
	}
	return t, nil
}

func parseOptionalTime(s *string) (*time.Time, error) {
	if s == nil || *s == "" {
		return nil, nil
	}
	t, err := time.Parse(time.RFC3339Nano, *s)
	if err != nil {
		return nil, err
	}
	return &t, nil
}

func (c *PulpClient) fetchTasks(ctx context.Context, params url.Values) ([]Task, error) {
	var tasks []Task
	params.Set("fields", taskFields)
	params.Set("limit", strconv.Itoa(taskPageSize))
	for page := 0; ; page++ {
		if page >= maxPages {
			return nil, fmt.Errorf("tasks query exceeded %d pages", maxPages)
		}
		params.Set("offset", strconv.Itoa(page*taskPageSize))
		var resp struct {
			Next    *string   `json:"next"`
			Results []rawTask `json:"results"`
		}
		if err := c.getJSON(ctx, c.BaseURL+c.TaskAPIPath+"?"+params.Encode(), &resp); err != nil {
			return nil, err
		}
		for _, r := range resp.Results {
			t, err := r.toTask()
			if err != nil {
				return nil, err
			}
			tasks = append(tasks, t)
		}
		if resp.Next == nil || *resp.Next == "" || len(resp.Results) < taskPageSize {
			return tasks, nil
		}
	}
}

// FinishedTasks returns tasks with finished_at in [start, end).
func (c *PulpClient) FinishedTasks(ctx context.Context, start, end time.Time) ([]Task, error) {
	var all []Task
	for chunkStart := start; chunkStart.Before(end); chunkStart = chunkStart.Add(finishedChunk) {
		chunkEnd := chunkStart.Add(finishedChunk)
		if chunkEnd.After(end) {
			chunkEnd = end
		}
		tasks, err := c.fetchTasks(ctx, url.Values{
			"finished_at__gte": {chunkStart.UTC().Format(time.RFC3339Nano)},
			"finished_at__lt":  {chunkEnd.UTC().Format(time.RFC3339Nano)},
			"ordering":         {"finished_at"},
		})
		if err != nil {
			return nil, fmt.Errorf("finished tasks %s..%s: %w", chunkStart.UTC().Format(time.RFC3339), chunkEnd.UTC().Format(time.RFC3339), err)
		}
		all = append(all, tasks...)
	}
	return all, nil
}

// TasksInState returns all tasks currently in the given state.
func (c *PulpClient) TasksInState(ctx context.Context, state string) ([]Task, error) {
	ordering := "pulp_created"
	if state == stateRunning {
		ordering = "-started_at"
	}
	tasks, err := c.fetchTasks(ctx, url.Values{"state": {state}, "ordering": {ordering}})
	if err != nil {
		return nil, fmt.Errorf("%s tasks: %w", state, err)
	}
	return tasks, nil
}

// OnlineWorkers returns the number of online workers from the status API. A
// missing online_workers list is an error: treating it as zero workers would
// silently change how waiting tasks are classified.
func (c *PulpClient) OnlineWorkers(ctx context.Context) (int, error) {
	var status struct {
		OnlineWorkers *[]json.RawMessage `json:"online_workers"`
	}
	if err := c.getJSON(ctx, c.BaseURL+c.StatusPath, &status); err != nil {
		return 0, fmt.Errorf("status: %w", err)
	}
	if status.OnlineWorkers == nil {
		return 0, fmt.Errorf("status: response has no online_workers")
	}
	return len(*status.OnlineWorkers), nil
}

// --- Resource name resolution ---

var (
	prnRe       = regexp.MustCompile(`^prn:([^:]+):(.+)$`)
	pdrnRe      = regexp.MustCompile(`^pdrn:([^:]+):(.*)$`)
	domainPrnRe = regexp.MustCompile(`^prn:core\.domain:(.+)$`)
)

const domainsAPIPath = "/api/pulp/default/api/v3/domains/"

var repoTypeAPIPaths = map[string]string{
	"rpm.rpmrepository":             "repositories/rpm/rpm/",
	"python.pythonrepository":       "repositories/python/python/",
	"file.filerepository":           "repositories/file/file/",
	"container.containerrepository": "repositories/container/container/",
	"ansible.ansiblerepository":     "repositories/ansible/ansible/",
}

func (c *PulpClient) fetchNames(ctx context.Context, apiPath string, uuids []string) (map[string]string, error) {
	result := map[string]string{}
	for i := 0; i < len(uuids); i += nameBatchSize {
		batch := uuids[i:min(i+nameBatchSize, len(uuids))]
		params := url.Values{
			"pulp_id__in": {strings.Join(batch, ",")},
			"fields":      {"pulp_href,name"},
			"limit":       {strconv.Itoa(len(batch))},
		}
		var resp struct {
			Results []struct {
				PulpHref string `json:"pulp_href"`
				Name     string `json:"name"`
			} `json:"results"`
		}
		if err := c.getJSON(ctx, c.BaseURL+apiPath+"?"+params.Encode(), &resp); err != nil {
			return result, err
		}
		for _, r := range resp.Results {
			parts := strings.Split(strings.Trim(r.PulpHref, "/"), "/")
			uuid := parts[len(parts)-1]
			name := r.Name
			if name == "" {
				name = uuid
			}
			result[uuid] = name
		}
	}
	return result, nil
}

// ResolveNames resolves domain and repository PRNs to names. Repository PRNs
// carry no domain, so each repository is looked up in every domain referenced
// by the same set of PRNs (tasks lock their domain alongside the repository).
func (c *PulpClient) ResolveNames(ctx context.Context, prns []string) map[string]string {
	names := map[string]string{}
	domainUUIDs := map[string]bool{}
	for _, prn := range prns {
		if m := domainPrnRe.FindStringSubmatch(prn); m != nil {
			domainUUIDs[m[1]] = true
		}
		if m := pdrnRe.FindStringSubmatch(prn); m != nil {
			domainUUIDs[m[1]] = true
		}
	}
	if len(domainUUIDs) == 0 {
		return names
	}
	domainNames, err := c.fetchNames(ctx, domainsAPIPath, sortedKeys(domainUUIDs))
	if err != nil {
		fmt.Fprintf(os.Stderr, "[pulp] warning: resolving domain names: %v\n", err)
	}
	for uuid, name := range domainNames {
		names["prn:core.domain:"+uuid] = name
	}
	for _, prn := range prns {
		if m := pdrnRe.FindStringSubmatch(prn); m != nil {
			if name, ok := domainNames[m[1]]; ok {
				names[prn] = name + "/" + m[2]
			}
		}
	}

	reposByType := map[string]map[string]bool{}
	for _, prn := range prns {
		m := prnRe.FindStringSubmatch(prn)
		if m == nil || repoTypeAPIPaths[m[1]] == "" {
			continue
		}
		if reposByType[m[1]] == nil {
			reposByType[m[1]] = map[string]bool{}
		}
		reposByType[m[1]][m[2]] = true
	}
	for _, typeKey := range sortedKeys(reposByType) {
		pending := reposByType[typeKey]
		for _, domainName := range sortedValues(domainNames) {
			if len(pending) == 0 {
				break
			}
			apiPath := "/api/pulp/" + url.PathEscape(domainName) + "/api/v3/" + repoTypeAPIPaths[typeKey]
			resolved, err := c.fetchNames(ctx, apiPath, sortedKeys(pending))
			if err != nil {
				fmt.Fprintf(os.Stderr, "[pulp] warning: resolving %s names in domain %s: %v\n", typeKey, domainName, err)
			}
			for uuid, name := range resolved {
				names["prn:"+typeKey+":"+uuid] = name
				delete(pending, uuid)
			}
		}
	}
	return names
}

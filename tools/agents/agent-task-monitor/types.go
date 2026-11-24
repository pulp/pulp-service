package main

import (
	"time"
)

// Task states reported by the Pulp tasks API.
const (
	stateWaiting   = "waiting"
	stateRunning   = "running"
	stateCompleted = "completed"
	stateFailed    = "failed"
	stateCanceled  = "canceled"
)

// Event types and severities, matching the swamp-based monitor.
const (
	eventUnexplainedWait  = "unexplained_wait"
	eventWorkerSaturation = "worker_saturation"

	severityInfo     = "info"
	severityWarning  = "warning"
	severityCritical = "critical"
)

// bucketSize is the snapshot granularity and the default sync window.
const bucketSize = 5 * time.Minute

// Task is the subset of a Pulp task the monitor works with. Resources come
// from reserved_resources_record: entries prefixed with "shared:" are shared
// locks, everything else is an exclusive lock.
type Task struct {
	Href       string
	State      string
	Name       string
	Created    time.Time
	StartedAt  *time.Time
	FinishedAt *time.Time
	Exclusive  []string
	Shared     []string
}

// Duration returns the run time in seconds, or false when the task never
// started or has not finished.
func (t Task) Duration() (float64, bool) {
	if t.StartedAt == nil || t.FinishedAt == nil {
		return 0, false
	}
	return t.FinishedAt.Sub(*t.StartedAt).Seconds(), true
}

// ResourceQueue counts the waiting tasks whose primary resource is Resource.
// Status is "anomaly" when at least one of them is an unexplained wait.
type ResourceQueue struct {
	Resource string `json:"resource"`
	Waiting  int    `json:"waiting"`
	Status   string `json:"status"`
}

// Snapshot is one 5-minute bucket. Finished-task counts are always filled in.
// The poll fields (waiting, running, workers, classification, queues) are only
// meaningful when Live is true, i.e. the bucket that held a sync's poll.
type Snapshot struct {
	WindowStart          time.Time       `json:"window_start"`
	WindowEnd            time.Time       `json:"window_end"`
	CompletedCount       int             `json:"completed_count"`
	FailedCount          int             `json:"failed_count"`
	CanceledCount        int             `json:"canceled_count"`
	TotalTasksPolled     int             `json:"total_tasks_polled"`
	Live                 bool            `json:"live,omitempty"`
	WaitingCount         int             `json:"waiting_count"`
	RunningCount         int             `json:"running_count"`
	OnlineWorkers        int             `json:"online_workers"`
	WorkerSaturation     float64         `json:"worker_saturation"`
	CapacityBlockedTasks int             `json:"capacity_blocked_tasks"`
	ResourceBlockedTasks int             `json:"resource_blocked_tasks"`
	QueueBlockedTasks    int             `json:"queue_blocked_tasks"`
	UnexplainedWaitTasks int             `json:"unexplained_wait_tasks"`
	ResourceQueues       []ResourceQueue `json:"resource_queues,omitempty"`
}

// Event is a detected anomaly.
type Event struct {
	Timestamp          time.Time `json:"timestamp"`
	EventType          string    `json:"event_type"`
	TaskHref           string    `json:"task_href"`
	TaskName           string    `json:"task_name"`
	RequestedResources []string  `json:"requested_resources"`
	PollWindowStart    time.Time `json:"poll_window_start"`
	Severity           string    `json:"severity"`
}

// TaskTypeStats aggregates finished tasks of one task name.
type TaskTypeStats struct {
	Count         int     `json:"count"`
	DurationSum   float64 `json:"duration_sum"`
	DurationCount int     `json:"duration_count"`
}

// ResourceCounts counts how many finished tasks locked a resource.
type ResourceCounts struct {
	Exclusive int `json:"exclusive"`
	Shared    int `json:"shared"`
}

// DailySummary aggregates one UTC day. Finished-task fields are keyed by
// finished_at; saturation fields come from the live poll of each sync.
// Monthly figures on the status page are derived from these.
type DailySummary struct {
	Date           string                     `json:"date"`
	Completed      int                        `json:"completed"`
	Failed         int                        `json:"failed"`
	Canceled       int                        `json:"canceled"`
	DurationSum    float64                    `json:"duration_sum"`
	DurationCount  int                        `json:"duration_count"`
	TaskTypes      map[string]*TaskTypeStats  `json:"task_types"`
	Resources      map[string]*ResourceCounts `json:"resources"`
	EventCount     int                        `json:"event_count"`
	PeakSaturation float64                    `json:"peak_saturation"`
	SaturationSum  float64                    `json:"saturation_sum"`
	PollCount      int                        `json:"poll_count"`
}

func newDailySummary(date string) *DailySummary {
	return &DailySummary{
		Date:      date,
		TaskTypes: map[string]*TaskTypeStats{},
		Resources: map[string]*ResourceCounts{},
	}
}

// ResourceName caches a resolved PRN display name. An empty Name records a
// failed lookup so it is not retried on every run.
type ResourceName struct {
	Name       string    `json:"name"`
	ResolvedAt time.Time `json:"resolved_at"`
}

// JiraState tracks the Jira issue for the current unexplained-wait incident.
type JiraState struct {
	IssueKey      string      `json:"issue_key,omitempty"`
	IncidentStart *time.Time  `json:"incident_start,omitempty"`
	LastUpdate    *time.Time  `json:"last_update,omitempty"`
	Creations     []time.Time `json:"creations,omitempty"`
}

const stateVersion = 1

// State is everything the monitor persists for one environment. It is stored
// as a single JSON document so a run either commits all of its changes or none.
type State struct {
	Version       int                      `json:"version"`
	Environment   string                   `json:"environment"`
	BaseURL       string                   `json:"base_url"`
	UpdatedAt     time.Time                `json:"updated_at"`
	LastWindowEnd *time.Time               `json:"last_window_end,omitempty"`
	Snapshots     []Snapshot               `json:"snapshots"`
	Events        []Event                  `json:"events"`
	Daily         map[string]*DailySummary `json:"daily"`
	ResourceNames map[string]ResourceName  `json:"resource_names,omitempty"`
	Jira          JiraState                `json:"jira"`
}

func newState(environment, baseURL string) *State {
	return &State{
		Version:       stateVersion,
		Environment:   environment,
		BaseURL:       baseURL,
		Daily:         map[string]*DailySummary{},
		ResourceNames: map[string]ResourceName{},
	}
}

// normalize fills in nil maps after decoding.
func (s *State) normalize() {
	if s.Daily == nil {
		s.Daily = map[string]*DailySummary{}
	}
	for date, d := range s.Daily {
		if d.TaskTypes == nil {
			d.TaskTypes = map[string]*TaskTypeStats{}
		}
		if d.Resources == nil {
			d.Resources = map[string]*ResourceCounts{}
		}
		d.Date = date
	}
	if s.ResourceNames == nil {
		s.ResourceNames = map[string]ResourceName{}
	}
}

func dateKey(t time.Time) string {
	return t.UTC().Format("2006-01-02")
}

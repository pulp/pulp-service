package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Metric names and attributes match the swamp monitor so existing dashboards
// and alerts keep working.

type otlpAttr struct {
	Key   string `json:"key"`
	Value struct {
		StringValue string `json:"stringValue"`
	} `json:"value"`
}

func attrs(kv ...string) []otlpAttr {
	out := make([]otlpAttr, 0, len(kv)/2)
	for i := 0; i+1 < len(kv); i += 2 {
		a := otlpAttr{Key: kv[i]}
		a.Value.StringValue = kv[i+1]
		out = append(out, a)
	}
	return out
}

type otlpDataPoint struct {
	AsDouble     float64    `json:"asDouble"`
	TimeUnixNano string     `json:"timeUnixNano"`
	Attributes   []otlpAttr `json:"attributes"`
}

type otlpMetric struct {
	Name        string `json:"name"`
	Description string `json:"description"`
	Gauge       struct {
		DataPoints []otlpDataPoint `json:"dataPoints"`
	} `json:"gauge"`
}

func gauge(name, description string, value float64, ts string, attributes []otlpAttr) otlpMetric {
	m := otlpMetric{Name: name, Description: description}
	m.Gauge.DataPoints = []otlpDataPoint{{AsDouble: value, TimeUnixNano: ts, Attributes: attributes}}
	return m
}

// buildOTLPPayload builds an OTLP/HTTP JSON metrics payload. includeSuccess
// adds pulp_monitor_last_success_timestamp (unix seconds), which a Prometheus
// alert compares against time() to catch a monitor that stopped running.
func buildOTLPPayload(res *SyncResult, environment string, now time.Time, includeSuccess bool) map[string]any {
	ts := strconv.FormatInt(now.UnixNano(), 10)
	base := attrs("environment", environment)
	var metrics []otlpMetric
	if includeSuccess {
		metrics = append(metrics, gauge("pulp_monitor_last_success_timestamp", "Unix time (seconds) of the last successful monitor run", float64(now.Unix()), ts, base))
	}
	metrics = append(metrics,
		gauge("pulp_tasks_waiting", "Current number of tasks in waiting state", float64(res.Waiting), ts, base),
		gauge("pulp_tasks_running", "Current number of tasks in running state", float64(res.Running), ts, base),
		gauge("pulp_workers_online", "Number of online Pulp workers", float64(res.OnlineWorkers), ts, base),
		gauge("pulp_worker_saturation", "Ratio of running tasks to online workers", res.Saturation, ts, base),
		gauge("pulp_unexplained_wait_tasks", "Tasks waiting with no resource conflict", float64(res.UnexplainedWait), ts, base),
	)
	byType := func(name, description string, counts map[string]int) {
		types := make([]string, 0, len(counts))
		for t := range counts {
			types = append(types, t)
		}
		sort.Strings(types)
		for _, t := range types {
			metrics = append(metrics, gauge(name, description, float64(counts[t]), ts, attrs("environment", environment, "task_type", t)))
		}
	}
	byType("pulp_tasks_completed_total", "Completed tasks by type in sync window", res.CompletedByType)
	byType("pulp_tasks_failed_total", "Failed tasks by type in sync window", res.FailedByType)

	return map[string]any{
		"resourceMetrics": []any{map[string]any{
			"resource": map[string]any{"attributes": attrs("service.name", "pulp-task-monitor", "service.instance.id", environment)},
			"scopeMetrics": []any{map[string]any{
				"scope":   map[string]any{"name": "pulp-task-monitor", "version": "2.0.0"},
				"metrics": metrics,
			}},
		}},
	}
}

// pushMetrics posts the payload to <collector>/v1/metrics, retrying transient
// failures. Unlike the swamp monitor, a failed push is an error.
func pushMetrics(ctx context.Context, collectorURL string, payload map[string]any) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	endpoint := strings.TrimRight(collectorURL, "/") + "/v1/metrics"
	client := &http.Client{Timeout: 30 * time.Second}
	var lastErr error
	for attempt := range 3 {
		if attempt > 0 {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(time.Duration(attempt) * retryBackoff):
			}
		}
		lastErr = func() error {
			req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
			if err != nil {
				return err
			}
			req.Header.Set("Content-Type", "application/json")
			resp, err := client.Do(req)
			if err != nil {
				return err
			}
			defer resp.Body.Close()
			respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
			if resp.StatusCode < 200 || resp.StatusCode > 299 {
				return fmt.Errorf("OTel collector %d: %s", resp.StatusCode, respBody)
			}
			return nil
		}()
		if lastErr == nil {
			return nil
		}
		fmt.Fprintf(os.Stderr, "[metrics] push failed (attempt %d/3): %v\n", attempt+1, lastErr)
	}
	return fmt.Errorf("push metrics to %s: %w", endpoint, lastErr)
}

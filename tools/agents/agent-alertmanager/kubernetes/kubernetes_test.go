package kubernetes

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	mcpClient "github.com/pulp/pulp-service/tools/agents/shared/mcp-client"
)

func TestNewClient(t *testing.T) {
	client := NewClient("crcp", "https://api.example.com:6443", "test-token", "pulp-prod")
	if client.Name() != "crcp" {
		t.Errorf("Name() = %q, want %q", client.Name(), "crcp")
	}
}

func TestSummarizePod_OOMKilled(t *testing.T) {
	pod := map[string]any{
		"status": map[string]any{
			"phase": "Running",
			"containerStatuses": []any{
				map[string]any{
					"name":         "pulp-api",
					"ready":        true,
					"restartCount": float64(3),
					"lastState": map[string]any{
						"terminated": map[string]any{
							"reason":   "OOMKilled",
							"exitCode": float64(137),
						},
					},
				},
			},
		},
	}

	result := summarizePod(pod)
	if !strings.Contains(result, "Running") {
		t.Errorf("should contain phase: %s", result)
	}
	if !strings.Contains(result, "restarts=3") {
		t.Errorf("should contain restart count: %s", result)
	}
	if !strings.Contains(result, "OOMKilled") {
		t.Errorf("should contain OOMKilled reason: %s", result)
	}
	if !strings.Contains(result, "137") {
		t.Errorf("should contain exit code 137: %s", result)
	}
}

func TestSummarizeEvents_FiltersWarningsOnly(t *testing.T) {
	events := []map[string]any{
		{
			"type":          "Normal",
			"reason":        "Pulled",
			"message":       "Container image already present",
			"count":         float64(1),
			"lastTimestamp":  "2026-05-28T21:02:58Z",
		},
		{
			"type":          "Warning",
			"reason":        "OOMKilling",
			"message":       "Memory exceeded",
			"count":         float64(3),
			"lastTimestamp":  "2026-05-28T21:02:58Z",
		},
	}

	result := summarizeEvents(events)
	if !strings.Contains(result, "OOMKilling") {
		t.Errorf("should contain Warning event: %s", result)
	}
	if strings.Contains(result, "Pulled") {
		t.Errorf("should not contain Normal event: %s", result)
	}
	if !strings.Contains(result, "count: 3") {
		t.Errorf("should contain event count: %s", result)
	}
}

func TestSummarizeEvents_NoWarnings(t *testing.T) {
	events := []map[string]any{
		{"type": "Normal", "reason": "Scheduled", "message": "ok", "count": float64(1), "lastTimestamp": "2026-05-28T21:00:00Z"},
	}

	result := summarizeEvents(events)
	if !strings.Contains(result, "No warning events") {
		t.Errorf("should indicate no warnings: %s", result)
	}
}

func TestRegisterTool_PodInfo(t *testing.T) {
	podResponse := map[string]any{
		"metadata": map[string]any{"name": "pulp-api-abc123", "namespace": "pulp-prod"},
		"status": map[string]any{
			"phase": "Running",
			"containerStatuses": []any{
				map[string]any{
					"name":         "pulp-api",
					"ready":        true,
					"restartCount": float64(3),
					"lastState": map[string]any{
						"terminated": map[string]any{
							"reason":   "OOMKilled",
							"exitCode": float64(137),
						},
					},
				},
			},
		},
	}

	eventsResponse := map[string]any{
		"items": []any{
			map[string]any{
				"type":         "Warning",
				"reason":       "OOMKilling",
				"message":      "Memory exceeded",
				"count":        float64(3),
				"lastTimestamp": "2026-05-28T21:02:58Z",
			},
		},
	}

	server := httptest.NewTLSServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Header.Get("Authorization") != "Bearer test-token" {
			writer.WriteHeader(http.StatusUnauthorized)
			return
		}
		writer.Header().Set("Content-Type", "application/json")
		if strings.Contains(request.URL.Path, "/pods/") {
			json.NewEncoder(writer).Encode(podResponse)
		} else if strings.Contains(request.URL.Path, "/events") {
			json.NewEncoder(writer).Encode(eventsResponse)
		}
	}))
	defer server.Close()

	client := NewClient("crcp", server.URL, "test-token", "pulp-prod")
	client.HTTPClient = server.Client()

	manager := mcpClient.NewMCPManager()
	tool := RegisterTool([]*Client{client}, manager)

	if tool.Function.Name != "k8s_get_pod_info" {
		t.Errorf("tool name = %q, want %q", tool.Function.Name, "k8s_get_pod_info")
	}

	result, err := manager.CallTool(context.Background(), "k8s_get_pod_info",
		`{"cluster":"crcp","namespace":"pulp-prod","pod":"pulp-api-abc123"}`)
	if err != nil {
		t.Fatalf("CallTool error: %v", err)
	}
	if !strings.Contains(result, "OOMKilled") {
		t.Errorf("result should contain OOMKilled: %s", result)
	}
	if !strings.Contains(result, "resolved_params") {
		t.Errorf("result should contain resolved_params: %s", result)
	}
}

func TestRegisterTool_UnknownCluster(t *testing.T) {
	client := NewClient("crcp", "https://api.example.com:6443", "token", "pulp-prod")
	manager := mcpClient.NewMCPManager()
	RegisterTool([]*Client{client}, manager)

	_, err := manager.CallTool(context.Background(), "k8s_get_pod_info",
		`{"cluster":"unknown"}`)
	if err == nil {
		t.Fatal("expected error for unknown cluster")
	}
	if !strings.Contains(err.Error(), "unknown cluster") {
		t.Errorf("error should mention unknown cluster: %v", err)
	}
}

func TestRegisterTool_NoPod(t *testing.T) {
	client := NewClient("crcp", "https://api.example.com:6443", "token", "pulp-prod")
	manager := mcpClient.NewMCPManager()
	RegisterTool([]*Client{client}, manager)

	result, err := manager.CallTool(context.Background(), "k8s_get_pod_info",
		`{"cluster":"crcp"}`)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(result, "No pod specified") {
		t.Errorf("result should indicate no pod: %s", result)
	}
}

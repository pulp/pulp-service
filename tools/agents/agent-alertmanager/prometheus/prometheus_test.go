package prometheus

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	mcpClient "github.com/pulp/pulp-service/tools/agents/shared/mcp-client"
)

func TestExpandShortcut_KnownShortcuts(t *testing.T) {
	tests := []struct {
		shortcut     string
		namespace    string
		wantContains string
	}{
		{"health", "pulp-prod", `up{namespace="pulp-prod"}`},
		{"errors", "pulp-prod", "pulp_api_errors_bucket:rate1h"},
		{"tasks", "pulp-prod", "pulp_waiting_tasks"},
		{"connections", "pulp-prod", "pulp_api_active_connections"},
	}
	for _, testCase := range tests {
		result := ExpandShortcut(testCase.shortcut, testCase.namespace)
		if !strings.Contains(result, testCase.wantContains) {
			t.Errorf("ExpandShortcut(%q) = %q, want contains %q", testCase.shortcut, result, testCase.wantContains)
		}
	}
}

func TestExpandShortcut_Passthrough(t *testing.T) {
	raw := `rate(http_requests_total[5m])`
	result := ExpandShortcut(raw, "pulp-prod")
	if result != raw {
		t.Errorf("raw query should pass through unchanged: got %q", result)
	}
}

func TestSummarizeResults_Vector(t *testing.T) {
	data := map[string]any{
		"data": map[string]any{
			"resultType": "vector",
			"result": []any{
				map[string]any{
					"metric": map[string]any{"__name__": "up", "namespace": "pulp-prod"},
					"value":  []any{1716931200.0, "1"},
				},
			},
		},
	}

	result := summarizeResults(data)
	if !strings.Contains(result, "vector") {
		t.Errorf("should contain result type: %s", result)
	}
	if !strings.Contains(result, "up") {
		t.Errorf("should contain metric name: %s", result)
	}
	if !strings.Contains(result, "1") {
		t.Errorf("should contain value: %s", result)
	}
}

func TestSummarizeResults_Empty(t *testing.T) {
	data := map[string]any{
		"data": map[string]any{
			"resultType": "vector",
			"result":     []any{},
		},
	}

	result := summarizeResults(data)
	if !strings.Contains(result, "No results") {
		t.Errorf("should indicate no results: %s", result)
	}
}

func TestRegisterTool_Query(t *testing.T) {
	promResponse := map[string]any{
		"status": "success",
		"data": map[string]any{
			"resultType": "vector",
			"result": []any{
				map[string]any{
					"metric": map[string]any{"__name__": "up", "namespace": "pulp-prod"},
					"value":  []any{1716931200.0, "1"},
				},
			},
		},
	}

	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		json.NewEncoder(writer).Encode(promResponse)
	}))
	defer server.Close()

	client := NewClient("crcp", server.URL, "test-token", "pulp-prod")

	manager := mcpClient.NewMCPManager()
	tool := RegisterTool([]*Client{client}, manager)

	if tool.Function.Name != "prometheus_query" {
		t.Errorf("tool name = %q", tool.Function.Name)
	}

	result, err := manager.CallTool(context.Background(), "prometheus_query",
		`{"cluster":"crcp","query":"health","mode":"instant"}`)
	if err != nil {
		t.Fatalf("CallTool error: %v", err)
	}
	if !strings.Contains(result, "resolved_params") {
		t.Errorf("result should contain resolved_params: %s", result)
	}
	if !strings.Contains(result, "up") {
		t.Errorf("result should contain metric name: %s", result)
	}
}

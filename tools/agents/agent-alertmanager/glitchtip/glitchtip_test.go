package glitchtip

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	mcpClient "github.com/pulp/pulp-service/tools/agents/shared/mcp-client"
)

func TestNewClientFromEnv_TokenOnly(t *testing.T) {
	t.Setenv("GLITCHTIP_TOKEN", "test-token")

	client, err := NewClientFromEnv()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if client == nil {
		t.Fatal("client should not be nil")
	}
}

func TestNewClientFromEnv_Missing(t *testing.T) {
	_, err := NewClientFromEnv()
	if err == nil {
		t.Fatal("expected error when GLITCHTIP_TOKEN not set")
	}
}

func TestRegisterTool_Issues(t *testing.T) {
	issuesResponse := []map[string]any{
		{
			"id":       "12345",
			"title":    "ValueError: invalid literal",
			"count":    float64(5),
			"lastSeen": "2026-05-28T21:02:58Z",
			"culprit":  "pulp_service/app/viewsets.py",
			"status":   "unresolved",
		},
	}

	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Header.Get("Authorization") != "Bearer test-token" {
			writer.WriteHeader(http.StatusUnauthorized)
			return
		}
		writer.Header().Set("Content-Type", "application/json")
		json.NewEncoder(writer).Encode(issuesResponse)
	}))
	defer server.Close()

	client := &Client{
		baseURL:    server.URL,
		token:      "test-token",
		orgSlug:    "insights",
		projects:   map[string]ProjectConfig{"stage": {ID: "84", Slug: "pulp-stage"}},
		httpClient: http.DefaultClient,
	}

	manager := mcpClient.NewMCPManager()
	tool := RegisterTool(client, manager)

	if tool.Function.Name != "glitchtip_get_errors" {
		t.Errorf("tool name = %q", tool.Function.Name)
	}

	result, err := manager.CallTool(context.Background(), "glitchtip_get_errors",
		`{"environment":"stage","action":"issues"}`)
	if err != nil {
		t.Fatalf("CallTool error: %v", err)
	}
	if !strings.Contains(result, "ValueError") {
		t.Errorf("result should contain error title: %s", result)
	}
	if !strings.Contains(result, "resolved_params") {
		t.Errorf("result should contain resolved_params: %s", result)
	}
}

func TestRegisterTool_UnknownEnvironment(t *testing.T) {
	client := &Client{
		baseURL:    "http://localhost",
		token:      "test-token",
		orgSlug:    "insights",
		projects:   map[string]ProjectConfig{"stage": {ID: "84", Slug: "pulp-stage"}},
		httpClient: http.DefaultClient,
	}

	manager := mcpClient.NewMCPManager()
	RegisterTool(client, manager)

	_, err := manager.CallTool(context.Background(), "glitchtip_get_errors",
		`{"environment":"prod"}`)
	if err == nil {
		t.Fatal("expected error for unknown environment")
	}
}

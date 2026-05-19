package main

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

func TestClusterConfig_String_RedactsToken(t *testing.T) {
	cfg := ClusterConfig{
		Name:  "prod",
		URL:   "https://am-prod.example.com",
		Token: "super-secret-token",
	}
	result := cfg.String()
	if result != "{name:prod url:https://am-prod.example.com token:REDACTED}" {
		t.Errorf("String() = %q, want token redacted", result)
	}
	if strings.Contains(result, "super-secret-token") {
		t.Error("String() leaked the token")
	}
}

func TestClusterConfig_GoString_RedactsToken(t *testing.T) {
	cfg := ClusterConfig{
		Name:  "prod",
		URL:   "https://am-prod.example.com",
		Token: "super-secret-token",
	}
	result := fmt.Sprintf("%#v", cfg)
	if strings.Contains(result, "super-secret-token") {
		t.Error("GoString() leaked the token")
	}
}

func TestClusterConfig_MarshalJSON_OmitsToken(t *testing.T) {
	cfg := ClusterConfig{
		Name:  "prod",
		URL:   "https://am-prod.example.com",
		Token: "super-secret-token",
	}
	jsonBytes, err := json.Marshal(cfg)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}
	if strings.Contains(string(jsonBytes), "super-secret-token") {
		t.Errorf("MarshalJSON leaked the token: %s", jsonBytes)
	}
	if strings.Contains(string(jsonBytes), "token") {
		t.Errorf("MarshalJSON included token field: %s", jsonBytes)
	}
}

func TestParseClusterConfigs_MultiCluster(t *testing.T) {
	t.Setenv("ALERTMANAGER_CLUSTERS", `[
		{"name":"prod","url":"https://am-prod.example.com","token":"token1"},
		{"name":"stage","url":"https://am-stage.example.com","token":"token2","insecure_skip_verify":true}
	]`)

	configs, err := parseClusterConfigs()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(configs) != 2 {
		t.Fatalf("expected 2 clusters, got %d", len(configs))
	}
	if configs[0].Name != "prod" || configs[0].Token != "token1" {
		t.Errorf("prod cluster: got %v", configs[0])
	}
	if configs[1].Name != "stage" || configs[1].InsecureSkipVerify != true {
		t.Errorf("stage cluster: got %v", configs[1])
	}
}

func TestParseClusterConfigs_SingleClusterFallback(t *testing.T) {
	t.Setenv("ALERTMANAGER_URL", "https://am.example.com")
	t.Setenv("ALERTMANAGER_TOKEN", "my-token")

	configs, err := parseClusterConfigs()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(configs) != 1 {
		t.Fatalf("expected 1 cluster, got %d", len(configs))
	}
	if configs[0].Name != "default" {
		t.Errorf("expected name 'default', got %q", configs[0].Name)
	}
}

func TestParseClusterConfigs_MultiClusterWinsOverSingle(t *testing.T) {
	t.Setenv("ALERTMANAGER_CLUSTERS", `[{"name":"prod","url":"https://am.example.com","token":"t1"}]`)
	t.Setenv("ALERTMANAGER_URL", "https://ignored.example.com")
	t.Setenv("ALERTMANAGER_TOKEN", "ignored-token")

	configs, err := parseClusterConfigs()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if configs[0].URL != "https://am.example.com" {
		t.Errorf("expected multi-cluster URL, got %q", configs[0].URL)
	}
}

func TestParseClusterConfigs_NeitherSet(t *testing.T) {
	_, err := parseClusterConfigs()
	if err == nil {
		t.Fatal("expected error when neither env var is set")
	}
}

func TestParseClusterConfigs_ValidationErrors(t *testing.T) {
	tests := []struct {
		name  string
		input string
	}{
		{"malformed JSON", `not json`},
		{"empty array", `[]`},
		{"empty name", `[{"name":"","url":"https://am","token":"t"}]`},
		{"invalid name chars", `[{"name":"PROD","url":"https://am","token":"t"}]`},
		{"duplicate names", `[{"name":"prod","url":"https://a","token":"t1"},{"name":"prod","url":"https://b","token":"t2"}]`},
		{"empty url", `[{"name":"prod","url":"","token":"t"}]`},
		{"empty token", `[{"name":"prod","url":"https://am","token":""}]`},
	}
	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Setenv("ALERTMANAGER_CLUSTERS", testCase.input)
			_, err := parseClusterConfigs()
			if err == nil {
				t.Errorf("expected validation error for %q", testCase.name)
			}
		})
	}
}

func TestParseClusterConfigs_GlobalInsecureInSingleCluster(t *testing.T) {
	t.Setenv("ALERTMANAGER_URL", "https://am.example.com")
	t.Setenv("ALERTMANAGER_TOKEN", "my-token")
	t.Setenv("ALERTMANAGER_INSECURE_SKIP_VERIFY", "true")

	configs, err := parseClusterConfigs()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !configs[0].InsecureSkipVerify {
		t.Error("expected InsecureSkipVerify=true in single-cluster fallback with global env var")
	}
}

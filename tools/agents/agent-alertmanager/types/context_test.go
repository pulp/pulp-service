package types

import (
	"strings"
	"testing"
	"time"
)

func TestAlertContext_FieldsExist(t *testing.T) {
	ctx := AlertContext{
		Cluster:   "crcp",
		Namespace: "pulp-prod",
		Pod:       "pulp-api-abc123",
		StartsAt:  "2026-05-28T20:57:58Z",
	}
	if ctx.Cluster != "crcp" {
		t.Errorf("Cluster = %q, want %q", ctx.Cluster, "crcp")
	}
	if ctx.Namespace != "pulp-prod" {
		t.Errorf("Namespace = %q, want %q", ctx.Namespace, "pulp-prod")
	}
	if ctx.Pod != "pulp-api-abc123" {
		t.Errorf("Pod = %q, want %q", ctx.Pod, "pulp-api-abc123")
	}
	if ctx.StartsAt != "2026-05-28T20:57:58Z" {
		t.Errorf("StartsAt = %q", ctx.StartsAt)
	}
}

func TestToolResponse_ToJSON(t *testing.T) {
	resp := ToolResponse{
		ResolvedParams: map[string]string{
			"cluster": "crcp",
			"pod":     "pulp-api-abc123",
		},
		Results:   "pod is healthy",
		Truncated: false,
	}
	result := resp.ToJSON()
	if !strings.Contains(result, `"cluster":"crcp"`) {
		t.Errorf("ToJSON missing cluster: %s", result)
	}
	if !strings.Contains(result, `"results":"pod is healthy"`) {
		t.Errorf("ToJSON missing results: %s", result)
	}
	if !strings.Contains(result, `"truncated":false`) {
		t.Errorf("ToJSON missing truncated: %s", result)
	}
}

func TestTruncate_ShortText(t *testing.T) {
	text, wasTruncated := Truncate("hello", 100)
	if wasTruncated {
		t.Error("short text should not be truncated")
	}
	if text != "hello" {
		t.Errorf("text = %q, want %q", text, "hello")
	}
}

func TestTruncate_LongText(t *testing.T) {
	text, wasTruncated := Truncate("abcdefghij", 5)
	if !wasTruncated {
		t.Error("long text should be truncated")
	}
	if !strings.HasPrefix(text, "abcde") {
		t.Errorf("truncated text should start with first 5 bytes: %q", text)
	}
	if !strings.Contains(text, "truncated") {
		t.Errorf("truncated text should contain truncation marker: %q", text)
	}
}

func TestDefaultTimeWindow_InstantAlerts(t *testing.T) {
	instantAlerts := []string{
		"PulpApiDown", "PulpCrashing", "PulpOOMKilled",
		"PulpWorkerDown", "PulpContentDown",
	}
	for _, alertName := range instantAlerts {
		result := DefaultTimeWindow(alertName)
		if result != 5*time.Minute {
			t.Errorf("DefaultTimeWindow(%q) = %v, want 5m", alertName, result)
		}
	}
}

func TestDefaultTimeWindow_BurnRateAlerts(t *testing.T) {
	burnAlerts := []string{
		"PulpApiErrorBudgetBurn1h",
		"PulpApiError6hBudgetBurn",
		"PulpContentErrorBudgetBurn",
	}
	for _, alertName := range burnAlerts {
		result := DefaultTimeWindow(alertName)
		if result != 15*time.Minute {
			t.Errorf("DefaultTimeWindow(%q) = %v, want 15m", alertName, result)
		}
	}
}

func TestDefaultTimeWindow_RDSAlerts(t *testing.T) {
	result := DefaultTimeWindow("PulpProdServiceRDSLowStorageSpace")
	if result != 30*time.Minute {
		t.Errorf("DefaultTimeWindow(RDS) = %v, want 30m", result)
	}
}

func TestDefaultTimeWindow_Unknown(t *testing.T) {
	result := DefaultTimeWindow("SomeUnknownAlert")
	if result != 5*time.Minute {
		t.Errorf("DefaultTimeWindow(unknown) = %v, want 5m default", result)
	}
}

func TestParseAlertContext_ValidJSON(t *testing.T) {
	ctx := ParseAlertContext(`{"cluster":"crcp","namespace":"pulp-prod","pod":"pulp-api-abc123"}`)
	if ctx.Cluster != "crcp" {
		t.Errorf("Cluster = %q, want %q", ctx.Cluster, "crcp")
	}
	if ctx.Namespace != "pulp-prod" {
		t.Errorf("Namespace = %q", ctx.Namespace)
	}
	if ctx.Pod != "pulp-api-abc123" {
		t.Errorf("Pod = %q", ctx.Pod)
	}
}

func TestParseAlertContext_EmptyString(t *testing.T) {
	ctx := ParseAlertContext("")
	if ctx.Cluster != "" {
		t.Errorf("Cluster should be empty, got %q", ctx.Cluster)
	}
}

func TestParseAlertContext_InvalidJSON(t *testing.T) {
	ctx := ParseAlertContext("not json")
	if ctx.Cluster != "" {
		t.Errorf("Cluster should be empty on invalid JSON, got %q", ctx.Cluster)
	}
}

package alertmanager

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestCheckAlerts_Found(t *testing.T) {
	testAlerts := []Alert{
		{
			Labels: map[string]string{
				"alertname": "PulpCrashing",
				"env":       "prod",
				"service":   "pulp",
			},
			Fingerprint: "abc123",
			StartsAt:    "2026-04-24T10:00:00Z",
		},
	}

	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		responseBytes, _ := json.Marshal(testAlerts)
		writer.Write(responseBytes)
	}))
	defer server.Close()

	client := NewClient("test", server.URL, "test-token", false)

	found, count, err := client.CheckAlerts(context.Background())
	if err != nil {
		t.Fatalf("CheckAlerts returned error: %v", err)
	}
	if !found {
		t.Error("expected found=true when alerts are present")
	}
	if count != 1 {
		t.Errorf("expected count=1, got %d", count)
	}
}

func TestCheckAlerts_Empty(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		writer.Write([]byte("[]"))
	}))
	defer server.Close()

	client := NewClient("test", server.URL, "test-token", false)

	found, count, err := client.CheckAlerts(context.Background())
	if err != nil {
		t.Fatalf("CheckAlerts returned error: %v", err)
	}
	if found {
		t.Error("expected found=false when no alerts are present")
	}
	if count != 0 {
		t.Errorf("expected count=0, got %d", count)
	}
}

func TestCheckAlerts_ServerError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.WriteHeader(http.StatusUnauthorized)
		writer.Write([]byte("unauthorized"))
	}))
	defer server.Close()

	client := NewClient("test", server.URL, "bad-token", false)
	_, _, err := client.CheckAlerts(context.Background())
	if err == nil {
		t.Fatal("expected error for 401 response")
	}
}

func TestCheckAlerts_AuthHeader(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		authHeader := request.Header.Get("Authorization")
		expectedAuth := "Bearer my-secret-token"
		if authHeader != expectedAuth {
			t.Errorf("expected Authorization header %q, got %q", expectedAuth, authHeader)
		}
		writer.Header().Set("Content-Type", "application/json")
		writer.Write([]byte("[]"))
	}))
	defer server.Close()

	client := NewClient("test", server.URL, "my-secret-token", false)

	_, _, err := client.CheckAlerts(context.Background())
	if err != nil {
		t.Fatalf("CheckAlerts returned error: %v", err)
	}
}

func TestNewClient_NameAccessor(t *testing.T) {
	client := NewClient("prod", "https://am.example.com", "token", false)
	if client.Name() != "prod" {
		t.Errorf("Name() = %q, want %q", client.Name(), "prod")
	}
}

func TestFetchAlerts_StampsClusterName(t *testing.T) {
	testAlerts := []Alert{
		{
			Labels:      map[string]string{"alertname": "PulpCrashing", "env": "prod"},
			Fingerprint: "abc123",
			StartsAt:    "2026-04-24T10:00:00Z",
		},
	}

	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		responseBytes, _ := json.Marshal(testAlerts)
		writer.Write(responseBytes)
	}))
	defer server.Close()

	client := NewClient("prod-cluster", server.URL, "test-token", false)
	alerts, err := client.FetchAlerts(context.Background())
	if err != nil {
		t.Fatalf("FetchAlerts returned error: %v", err)
	}
	if len(alerts) != 1 {
		t.Fatalf("expected 1 alert, got %d", len(alerts))
	}
	if alerts[0].Cluster != "prod-cluster" {
		t.Errorf("expected Cluster=%q, got %q", "prod-cluster", alerts[0].Cluster)
	}
}

func TestFetchAlerts_SanitizesTokenInError(t *testing.T) {
	secretToken := "super-secret-bearer-token"
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.WriteHeader(http.StatusForbidden)
		writer.Write([]byte("Invalid token: " + secretToken))
	}))
	defer server.Close()

	client := NewClient("prod", server.URL, secretToken, false)
	_, err := client.FetchAlerts(context.Background())
	if err == nil {
		t.Fatal("expected error for 403 response")
	}
	if strings.Contains(err.Error(), secretToken) {
		t.Errorf("error message leaked the token: %v", err)
	}
	if !strings.Contains(err.Error(), "[REDACTED]") {
		t.Errorf("error message should contain [REDACTED]: %v", err)
	}
}

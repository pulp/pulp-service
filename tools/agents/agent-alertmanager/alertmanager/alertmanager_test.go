package alertmanager

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
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

	client := NewClient(server.URL, "test-token")

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

	client := NewClient(server.URL, "test-token")

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

	client := NewClient(server.URL, "bad-token")
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

	client := NewClient(server.URL, "my-secret-token")

	_, _, err := client.CheckAlerts(context.Background())
	if err != nil {
		t.Fatalf("CheckAlerts returned error: %v", err)
	}
}

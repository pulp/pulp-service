package auth

import "testing"

func TestResolveFromEnv_ExplicitProjectID(t *testing.T) {
	t.Setenv("ANTHROPIC_VERTEX_PROJECT_ID", "my-project")
	t.Setenv("VERTEX_SA_JSON", "")
	t.Setenv("ANTHROPIC_API_KEY", "")

	config, err := ResolveFromEnv()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if config.ProjectID != "my-project" {
		t.Errorf("ProjectID = %q, want %q", config.ProjectID, "my-project")
	}
}

func TestResolveFromEnv_ProjectIDFromSAJSON(t *testing.T) {
	t.Setenv("ANTHROPIC_VERTEX_PROJECT_ID", "")
	t.Setenv("ANTHROPIC_API_KEY", "")
	t.Setenv("VERTEX_SA_JSON", `{"project_id":"sa-project","type":"service_account"}`)

	config, err := ResolveFromEnv()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if config.ProjectID != "sa-project" {
		t.Errorf("ProjectID = %q, want %q", config.ProjectID, "sa-project")
	}
	if len(config.SACredential) == 0 {
		t.Error("SACredential should be set")
	}
}

func TestResolveFromEnv_ProxyMode(t *testing.T) {
	t.Setenv("ANTHROPIC_API_KEY", "sk-test-key")
	t.Setenv("ANTHROPIC_BASE_URL", "http://localhost:8443/v1/")
	t.Setenv("ANTHROPIC_VERTEX_PROJECT_ID", "")
	t.Setenv("VERTEX_SA_JSON", "")

	config, err := ResolveFromEnv()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if config.APIKey != "sk-test-key" {
		t.Errorf("APIKey = %q", config.APIKey)
	}
	if config.BaseURL != "http://localhost:8443/v1" {
		t.Errorf("BaseURL = %q, want trailing slash removed", config.BaseURL)
	}
}

func TestResolveFromEnv_NoAuth(t *testing.T) {
	t.Setenv("ANTHROPIC_API_KEY", "")
	t.Setenv("ANTHROPIC_VERTEX_PROJECT_ID", "")
	t.Setenv("VERTEX_SA_JSON", "")

	_, err := ResolveFromEnv()
	if err == nil {
		t.Fatal("expected error when no auth configured")
	}
}

func TestResolveFromEnv_InvalidSAJSON(t *testing.T) {
	t.Setenv("ANTHROPIC_API_KEY", "")
	t.Setenv("ANTHROPIC_VERTEX_PROJECT_ID", "")
	t.Setenv("VERTEX_SA_JSON", "not json")

	_, err := ResolveFromEnv()
	if err == nil {
		t.Fatal("expected error for invalid SA JSON")
	}
}

func TestResolveFromEnv_DefaultRegion(t *testing.T) {
	t.Setenv("ANTHROPIC_VERTEX_PROJECT_ID", "my-project")
	t.Setenv("CLOUD_ML_REGION", "")
	t.Setenv("VERTEX_SA_JSON", "")
	t.Setenv("ANTHROPIC_API_KEY", "")

	config, err := ResolveFromEnv()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if config.Region != "us-east5" {
		t.Errorf("Region = %q, want %q", config.Region, "us-east5")
	}
}

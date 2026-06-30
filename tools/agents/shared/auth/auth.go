package auth

import (
	"encoding/json"
	"fmt"
	"os"
)

type Config struct {
	ProjectID    string
	Region       string
	APIKey       string
	BaseURL      string
	SACredential []byte
}

func ResolveFromEnv() (*Config, error) {
	config := &Config{
		APIKey:    os.Getenv("ANTHROPIC_API_KEY"),
		BaseURL:   normalizeBaseURL(os.Getenv("ANTHROPIC_BASE_URL")),
		ProjectID: os.Getenv("ANTHROPIC_VERTEX_PROJECT_ID"),
	}

	if saJSON := os.Getenv("VERTEX_SA_JSON"); saJSON != "" {
		config.SACredential = []byte(saJSON)
	}

	if config.ProjectID == "" && len(config.SACredential) > 0 {
		var serviceAccount struct {
			ProjectID string `json:"project_id"`
		}
		if err := json.Unmarshal(config.SACredential, &serviceAccount); err != nil {
			return nil, fmt.Errorf("VERTEX_SA_JSON is not valid JSON: %w", err)
		}
		if serviceAccount.ProjectID == "" {
			return nil, fmt.Errorf("VERTEX_SA_JSON does not contain a project_id field")
		}
		config.ProjectID = serviceAccount.ProjectID
	}

	config.Region = os.Getenv("CLOUD_ML_REGION")
	if config.Region == "" {
		config.Region = "us-east5"
	}

	if config.APIKey == "" && config.ProjectID == "" {
		return nil, fmt.Errorf("set ANTHROPIC_API_KEY (proxy mode) or ANTHROPIC_VERTEX_PROJECT_ID / VERTEX_SA_JSON (Vertex AI mode)")
	}

	return config, nil
}

func normalizeBaseURL(url string) string {
	if url == "" {
		return ""
	}
	if url[len(url)-1] == '/' {
		return url[:len(url)-1]
	}
	return url
}

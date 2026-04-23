package anthropicClient

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sync"
	"time"

	"golang.org/x/oauth2/google"
)

// VertexClient implements the anthropicclient.Doer interface.
// It intercepts HTTP requests meant for the Anthropic API and rewrites
// them to target the Vertex AI endpoint, using gcloud credentials for auth.
type VertexClient struct {
	ProjectID    string
	Region       string
	Model        string
	SACredential []byte // Raw service account JSON; if set, used instead of ADC.

	once    sync.Once
	creds   *google.Credentials
	initErr error
}

const (
	ANTHROPIC_VERSION   = "vertex-2023-10-16"
	GOOGLE_API_AUTH_URL = "https://www.googleapis.com/auth/cloud-platform"
)

func (v *VertexClient) Do(req *http.Request) (*http.Response, error) {
	ctx := req.Context()

	v.once.Do(func() {
		initCtx := context.Background()
		var creds *google.Credentials
		var err error
		if len(v.SACredential) > 0 {
			creds, err = google.CredentialsFromJSON(initCtx, v.SACredential, GOOGLE_API_AUTH_URL)
		} else {
			creds, err = google.FindDefaultCredentials(initCtx, GOOGLE_API_AUTH_URL)
		}
		if err != nil {
			v.initErr = fmt.Errorf("obtain credentials: %w", err)
			return
		}
		v.creds = creds
	})
	if v.initErr != nil {
		return nil, v.initErr
	}
	token, err := v.creds.TokenSource.Token()
	if err != nil {
		return nil, fmt.Errorf("get access token: %w", err)
	}

	body, err := io.ReadAll(req.Body)
	if err != nil {
		return nil, fmt.Errorf("read request body: %w", err)
	}
	var payload map[string]any
	if err := json.Unmarshal(body, &payload); err != nil {
		return nil, fmt.Errorf("unmarshal request body: %w", err)
	}
	payload["anthropic_version"] = ANTHROPIC_VERSION
	delete(payload, "model")
	modifiedBody, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("marshal modified body: %w", err)
	}

	vertexURL := fmt.Sprintf(
		"https://%s-aiplatform.googleapis.com/v1/projects/%s/locations/%s/publishers/anthropic/models/%s:rawPredict",
		v.Region, v.ProjectID, v.Region, v.Model,
	)

	newReq, err := http.NewRequestWithContext(ctx, http.MethodPost, vertexURL, bytes.NewReader(modifiedBody))
	if err != nil {
		return nil, fmt.Errorf("create vertex request: %w", err)
	}
	newReq.Header.Set("Content-Type", "application/json")
	newReq.Header.Set("Authorization", "Bearer "+token.AccessToken)

	httpClient := http.Client{
		Timeout: time.Duration(time.Minute * 2),
	}
	return httpClient.Do(newReq)
}

package models

import (
	"context"
	"fmt"

	"github.com/tmc/langchaingo/llms/anthropic"

	anthropicClient "agent-scan/anthropic-client"
)

type Claude struct {
	Model     string
	ProjectID string
	Region    string
}

func (claude Claude) ModelRun(ctx context.Context, cfg RunConfig) (string, error) {

	// Claude via Vertex AI.
	llm, err := anthropic.New(
		anthropic.WithModel(claude.Model),
		anthropic.WithToken("vertex-ai"),
		anthropic.WithHTTPClient(&anthropicClient.VertexClient{
			ProjectID: claude.ProjectID,
			Region:    claude.Region,
			Model:     claude.Model,
		}),
	)
	if err != nil {
		return "", fmt.Errorf("init anthropic client: %w", err)
	}

	return Run(ctx, llm, cfg)
}

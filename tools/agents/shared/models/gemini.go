package models

import (
	"context"
	"fmt"

	"github.com/tmc/langchaingo/llms/googleai"
	"github.com/tmc/langchaingo/llms/googleai/vertex"
)

type Gemini struct {
	Model     string
	ProjectID string
	Region    string
}

func (gemini Gemini) ModelRun(ctx context.Context, cfg RunConfig) (string, error) {

	// Gemini via Vertex AI.
	llm, err := vertex.New(ctx,
		googleai.WithCloudProject(gemini.ProjectID),
		googleai.WithCloudLocation(gemini.Region),
		googleai.WithDefaultModel(gemini.Model),
	)
	if err != nil {
		return "", fmt.Errorf("init gemini client: %w", err)
	}

	return Run(ctx, llm, cfg)
}

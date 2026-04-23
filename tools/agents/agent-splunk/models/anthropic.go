package models

import (
	"context"
	"fmt"

	"github.com/tmc/langchaingo/llms/anthropic"

	anthropicClient "agent-splunk/anthropic-client"
)

type Claude struct {
	Model        string
	ProjectID    string
	Region       string
	SACredential []byte
	APIKey       string
	BaseURL      string
}

func (claude Claude) ModelRun(ctx context.Context, cfg RunConfig) (string, error) {
	var opts []anthropic.Option
	opts = append(opts, anthropic.WithModel(claude.Model))

	if claude.APIKey != "" {
		opts = append(opts, anthropic.WithToken(claude.APIKey))
		if claude.BaseURL != "" {
			opts = append(opts, anthropic.WithBaseURL(claude.BaseURL))
		}
	} else {
		opts = append(opts,
			anthropic.WithToken("vertex-ai"),
			anthropic.WithHTTPClient(&anthropicClient.VertexClient{
				ProjectID:    claude.ProjectID,
				Region:       claude.Region,
				Model:        claude.Model,
				SACredential: claude.SACredential,
			}),
		)
	}

	llm, err := anthropic.New(opts...)
	if err != nil {
		return "", fmt.Errorf("init anthropic client: %w", err)
	}

	return Run(ctx, llm, cfg)
}

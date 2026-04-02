package models

import (
	"context"
	"fmt"
	"time"

	"github.com/tmc/langchaingo/llms"
)

// ToolExecutor is a function that executes a tool call and returns the result.
type ToolExecutor func(name, argsJSON string) (string, error)

// RunConfig holds per-request data that is common to all model implementations.
type RunConfig struct {
	Prompt         string
	Tools          []llms.Tool
	SystemMessages []llms.MessageContent
	ExecuteTool    ToolExecutor
}

const maxToolIterations = 100

type Model interface {
	ModelRun(context.Context, RunConfig) (string, error)
}

func Run(ctx context.Context, llm llms.Model, cfg RunConfig) (string, error) {
	// Apply a default timeout if the context has no deadline.
	if _, ok := ctx.Deadline(); !ok {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, 10*time.Minute)
		defer cancel()
	}

	var history []llms.MessageContent
	history = append(history, cfg.SystemMessages...)
	history = append(history, llms.TextParts(llms.ChatMessageTypeHuman, cfg.Prompt))

	opts := []llms.CallOption{llms.WithMaxTokens(4096)}
	if len(cfg.Tools) > 0 {
		opts = append(opts, llms.WithTools(cfg.Tools))
	}

	for i := 0; i < maxToolIterations; i++ {
		resp, err := llm.GenerateContent(ctx, history, opts...)
		if err != nil {
			return "", fmt.Errorf("generate content (iteration %d): %w", i, err)
		}

		if len(resp.Choices) == 0 {
			return "", fmt.Errorf("no choices in response (iteration %d)", i)
		}

		// Collect tool calls across all choices.
		var calls []llms.ToolCall
		for _, c := range resp.Choices {
			calls = append(calls, c.ToolCalls...)
		}

		// No tool calls — return the final text answer.
		if len(calls) == 0 {
			choice := resp.Choices[0]
			fmt.Println(choice.Content)
			return choice.Content, nil
		}

		// Execute each tool call and append results.
		for _, tc := range calls {
			toolName := tc.FunctionCall.Name
			toolArgs := tc.FunctionCall.Arguments
			history = append(history, llms.MessageContent{
				Role:  llms.ChatMessageTypeAI,
				Parts: []llms.ContentPart{tc},
			})

			fmt.Printf("  [tool] %s(%s)\n", toolName, toolArgs)

			result, execErr := cfg.ExecuteTool(toolName, toolArgs)
			if execErr != nil {
				result = fmt.Sprintf("error: %v", execErr)
			}

			history = append(history, llms.MessageContent{
				Role: llms.ChatMessageTypeTool,
				Parts: []llms.ContentPart{
					llms.ToolCallResponse{
						ToolCallID: tc.ID,
						Name:       toolName,
						Content:    result,
					},
				},
			})
		}
	}

	return "", fmt.Errorf("tool loop exceeded %d iterations", maxToolIterations)
}

package models

import (
	"context"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/tmc/langchaingo/llms"

	mcpClient "agent-splunk/mcp-client"
)

// RunConfig holds per-request data that is common to all model implementations.
type RunConfig struct {
	Prompt         string
	Tools          []llms.Tool
	MCP            *mcpClient.MCPManager
	SystemMessages []llms.MessageContent
}

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

	// Agentic loop: Model may call MCP tools multiple times before
	// producing a final text answer.
	const maxIterations = 100
	for range maxIterations {
		resp, err := llm.GenerateContent(ctx, history, opts...)
		if err != nil {
			return "", fmt.Errorf("generate content: %w", err)
		}

		// Collect tool calls across all choices.
		var calls []llms.ToolCall
		for _, c := range resp.Choices {
			calls = append(calls, c.ToolCalls...)
		}

		// No tool calls → final answer.
		if len(calls) == 0 {
			var answer strings.Builder
			for _, c := range resp.Choices {
				if c.Content != "" {
					fmt.Println(c.Content)
					answer.WriteString(c.Content)
				}
			}
			return answer.String(), nil
		}

		// Forward each tool call to the MCP server.
		for _, tc := range calls {
			history = append(history, llms.MessageContent{
				Role: llms.ChatMessageTypeAI,
				Parts: []llms.ContentPart{
					llms.ToolCall{
						ID:   tc.ID,
						Type: tc.Type,
						FunctionCall: &llms.FunctionCall{
							Name:      tc.FunctionCall.Name,
							Arguments: tc.FunctionCall.Arguments,
						},
					},
				},
			})

			result, execErr := cfg.MCP.CallTool(ctx, tc.FunctionCall.Name, tc.FunctionCall.Arguments)
			if execErr != nil {
				result = fmt.Sprintf("Error: %v", execErr)
			}

			fmt.Fprintf(os.Stderr, "[tool] %s → %d bytes\n", tc.FunctionCall.Name, len(result))

			history = append(history, llms.MessageContent{
				Role: llms.ChatMessageTypeTool,
				Parts: []llms.ContentPart{
					llms.ToolCallResponse{
						ToolCallID: tc.ID,
						Name:       tc.FunctionCall.Name,
						Content:    result,
					},
				},
			})
		}
	}

	return "", fmt.Errorf("reached maximum tool-call iterations (%d) without a final answer", maxIterations)
}

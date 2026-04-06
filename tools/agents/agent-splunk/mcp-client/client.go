package mcpClient

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/tmc/langchaingo/llms"

	"agent-splunk/utils"
)

// ---------------------------------------------------------------------------
// MCP client — connects to multiple MCP servers (e.g. Grafana, Jira) over
// HTTP and bridges their tools into the langchaingo tool-calling loop.
// ---------------------------------------------------------------------------

// NativeToolHandler is a function that handles a tool call directly in Go,
// without going through an MCP server.
type NativeToolHandler func(ctx context.Context, argsJSON string) (string, error)

// MCPManager holds multiple MCP sessions and routes tool calls to the
// correct server based on tool name.
type MCPManager struct {
	sessions []*mcp.ClientSession
	// toolMap maps a tool name to the session that owns it.
	toolMap map[string]*mcp.ClientSession
	// nativeTools maps a tool name to a Go function handler.
	nativeTools map[string]NativeToolHandler
}

// NewMCPManager creates a bare MCPManager with no sessions, useful when only
// native tools are needed.
func NewMCPManager() *MCPManager {
	return &MCPManager{
		toolMap:     make(map[string]*mcp.ClientSession),
		nativeTools: make(map[string]NativeToolHandler),
	}
}

// Close closes all managed MCP sessions.
func (m *MCPManager) Close() {
	for _, s := range m.sessions {
		s.Close()
	}
}

// ConnectMCP connects to all configured MCP servers over HTTP.
//
// Jira:    JIRA_MCP_URL → Streamable HTTP transport
//
// Environment variables for Jira (must be configured on the MCP server side):
//
//	JIRA_URL, JIRA_USERNAME, JIRA_TOKEN
func ConnectMCP(ctx context.Context) (*MCPManager, error) {
	mgr := &MCPManager{
		toolMap:     make(map[string]*mcp.ClientSession),
		nativeTools: make(map[string]NativeToolHandler),
	}

	// --- Jira MCP (HTTP) ---
	if url := os.Getenv("JIRA_MCP_URL"); url != "" {
		session, err := connectHTTP(ctx, url)
		if err != nil {
			return nil, fmt.Errorf("jira MCP: %w", err)
		}
		mgr.sessions = append(mgr.sessions, session)
		fmt.Fprintf(os.Stderr, "[mcp] connected to Jira (http: %s)\n", url)
	}

	if len(mgr.sessions) == 0 {
		return nil, nil // no MCP servers configured
	}
	return mgr, nil
}

func connectHTTP(ctx context.Context, endpoint string) (*mcp.ClientSession, error) {
	client := mcp.NewClient(&mcp.Implementation{
		Name:    "agent-splunk",
		Version: "v1.0.0",
	}, nil)

	transport := &mcp.StreamableClientTransport{
		Endpoint: endpoint,
	}

	session, err := client.Connect(ctx, transport, nil)
	if err != nil {
		return nil, err
	}
	return session, nil
}

// DiscoverTools lists all tools from every connected MCP server and converts
// them to langchaingo tool definitions that Claude can call.
func (m *MCPManager) DiscoverTools(ctx context.Context) ([]llms.Tool, error) {
	var tools []llms.Tool
	for _, session := range m.sessions {
		for tool, err := range session.Tools(ctx, nil) {
			if err != nil {
				return nil, fmt.Errorf("list MCP tools: %w", err)
			}
			m.toolMap[tool.Name] = session
			tools = append(tools, llms.Tool{
				Type: "function",
				Function: &llms.FunctionDefinition{
					Name:        tool.Name,
					Description: tool.Description,
					Parameters:  tool.InputSchema,
				},
			})
		}
	}
	return tools, nil
}

// RegisterNativeTool registers a Go-native tool handler with its langchaingo
// tool definition so it can be discovered and called alongside MCP tools.
func (m *MCPManager) RegisterNativeTool(tool llms.Tool, handler NativeToolHandler) {
	m.nativeTools[tool.Function.Name] = handler
}

// CallTool forwards a tool call to the MCP server or native handler that owns
// the tool.
func (m *MCPManager) CallTool(ctx context.Context, name, argsJSON string) (string, error) {
	// Check native tools first.
	if handler, ok := m.nativeTools[name]; ok {
		result, err := handler(ctx, argsJSON)
		if err != nil {
			return "", err
		}
		return utils.Truncate(result, 30000), nil
	}

	session, ok := m.toolMap[name]
	if !ok {
		return "", fmt.Errorf("unknown tool: %s", name)
	}

	var args map[string]any
	if argsJSON != "" {
		if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
			return "", fmt.Errorf("parse tool arguments: %w", err)
		}
	}

	result, err := session.CallTool(ctx, &mcp.CallToolParams{
		Name:      name,
		Arguments: args,
	})
	if err != nil {
		return "", err
	}

	var parts []string
	for _, c := range result.Content {
		if text, ok := c.(*mcp.TextContent); ok {
			parts = append(parts, text.Text)
		} else {
			b, _ := json.Marshal(c)
			parts = append(parts, string(b))
		}
	}
	text := strings.Join(parts, "\n")

	if result.IsError {
		return "", fmt.Errorf("tool error: %s", text)
	}
	return utils.Truncate(text, 30000), nil
}

package cloudwatch

import (
	"context"
	"strings"
	"testing"

	"github.com/aws/aws-sdk-go-v2/service/cloudwatchlogs"
	cloudwatchTypes "github.com/aws/aws-sdk-go-v2/service/cloudwatchlogs/types"

	mcpClient "github.com/pulp/pulp-service/tools/agents/shared/mcp-client"
)

func strPtr(val string) *string { return &val }

type mockLogsAPI struct {
	queryID string
	status  cloudwatchTypes.QueryStatus
	results [][]cloudwatchTypes.ResultField
}

func (mock *mockLogsAPI) StartQuery(ctx context.Context, params *cloudwatchlogs.StartQueryInput, optFns ...func(*cloudwatchlogs.Options)) (*cloudwatchlogs.StartQueryOutput, error) {
	return &cloudwatchlogs.StartQueryOutput{QueryId: &mock.queryID}, nil
}

func (mock *mockLogsAPI) GetQueryResults(ctx context.Context, params *cloudwatchlogs.GetQueryResultsInput, optFns ...func(*cloudwatchlogs.Options)) (*cloudwatchlogs.GetQueryResultsOutput, error) {
	return &cloudwatchlogs.GetQueryResultsOutput{
		Status:  mock.status,
		Results: mock.results,
	}, nil
}

func TestExpandShortcut_KnownShortcuts(t *testing.T) {
	result := ExpandShortcut("errors", "prod")
	if !strings.Contains(result, "ERROR") {
		t.Errorf("errors shortcut should contain ERROR filter: %s", result)
	}
	if !strings.Contains(result, "Exception") {
		t.Errorf("errors shortcut should contain Exception filter: %s", result)
	}
}

func TestExpandShortcut_5xx(t *testing.T) {
	result := ExpandShortcut("5xx", "prod")
	if !strings.Contains(result, "5[0-9][0-9]") {
		t.Errorf("5xx shortcut should contain 5xx pattern: %s", result)
	}
}

func TestExpandShortcut_Passthrough(t *testing.T) {
	raw := "filter @message like /custom/"
	result := ExpandShortcut(raw, "prod")
	if result != raw {
		t.Errorf("raw query should pass through: got %q", result)
	}
}

func TestRegisterTool_Query(t *testing.T) {
	mock := &mockLogsAPI{
		queryID: "test-query-id",
		status:  cloudwatchTypes.QueryStatusComplete,
		results: [][]cloudwatchTypes.ResultField{
			{
				{Field: strPtr("@timestamp"), Value: strPtr("2026-05-28T21:02:58.000Z")},
				{Field: strPtr("@message"), Value: strPtr("ERROR: OOMKilled container pulp-api")},
			},
		},
	}

	client := &Client{
		logsAPI:   mock,
		logGroups: map[string][]string{"prod": {"crcp01ue1.pulp-prod"}},
	}

	manager := mcpClient.NewMCPManager()
	tool := RegisterTool(client, manager)

	if tool.Function.Name != "cloudwatch_query_logs" {
		t.Errorf("tool name = %q", tool.Function.Name)
	}

	result, err := manager.CallTool(context.Background(), "cloudwatch_query_logs",
		`{"environment":"prod","query":"errors"}`)
	if err != nil {
		t.Fatalf("CallTool error: %v", err)
	}
	if !strings.Contains(result, "OOMKilled") {
		t.Errorf("result should contain OOMKilled: %s", result)
	}
	if !strings.Contains(result, "resolved_params") {
		t.Errorf("result should contain resolved_params: %s", result)
	}
}

func TestRegisterTool_UnknownEnvironment(t *testing.T) {
	client := &Client{
		logsAPI:   &mockLogsAPI{},
		logGroups: map[string][]string{"prod": {"crcp01ue1.pulp-prod"}},
	}

	manager := mcpClient.NewMCPManager()
	RegisterTool(client, manager)

	_, err := manager.CallTool(context.Background(), "cloudwatch_query_logs",
		`{"environment":"staging","query":"errors"}`)
	if err == nil {
		t.Fatal("expected error for unknown environment")
	}
}

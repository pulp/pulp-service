package cloudwatch

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/cloudwatchlogs"
	cwTypes "github.com/aws/aws-sdk-go-v2/service/cloudwatchlogs/types"
)

type fakeLogs struct {
	input *cloudwatchlogs.StartQueryInput
	rows  [][]cwTypes.ResultField
}

func (f *fakeLogs) StartQuery(_ context.Context, in *cloudwatchlogs.StartQueryInput,
	_ ...func(*cloudwatchlogs.Options)) (*cloudwatchlogs.StartQueryOutput, error) {
	f.input = in
	return &cloudwatchlogs.StartQueryOutput{QueryId: aws.String("q1")}, nil
}

func (f *fakeLogs) GetQueryResults(_ context.Context, _ *cloudwatchlogs.GetQueryResultsInput,
	_ ...func(*cloudwatchlogs.Options)) (*cloudwatchlogs.GetQueryResultsOutput, error) {
	return &cloudwatchlogs.GetQueryResultsOutput{Status: cwTypes.QueryStatusComplete, Results: f.rows}, nil
}

func TestExpandQueryNSGWAlwaysFiltersGatewayStream(t *testing.T) {
	for _, q := range []string{"raw", "errors", "errors-list", "fields @timestamp, message", "| fields message"} {
		got, err := ExpandQuery(SourceNSGW, "prod", q, "", 10)
		if err != nil {
			t.Fatalf("%q: %v", q, err)
		}
		if !strings.HasPrefix(got, nsgwStreamFilter) {
			t.Errorf("%q: query does not start with gateway stream filter:\n%s", q, got)
		}
		if strings.Contains(got, "\n| |") {
			t.Errorf("%q: duplicated pipe:\n%s", q, got)
		}
	}
}

func TestExpandQueryTraceRequiresMatch(t *testing.T) {
	for _, src := range []string{SourceNSGW, SourcePulp} {
		if _, err := ExpandQuery(src, "prod", "trace", "", 10); err == nil {
			t.Errorf("%s trace without match: expected error", src)
		}
	}
}

func TestExpandQueryEscapesMatch(t *testing.T) {
	got, err := ExpandQuery(SourceNSGW, "prod", "trace", `/api/pulp/"x"\y`, 10)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(got, `filter message like "/api/pulp/\"x\"\\y"`) {
		t.Errorf("match not escaped:\n%s", got)
	}
}

func TestExpandQueryPulpUsesEnvironmentStreams(t *testing.T) {
	got, err := ExpandQuery(SourcePulp, "stage", "errors", "", 10)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(got, "/pulp-stage_pulp-(api|content|worker)/") {
		t.Errorf("missing stage stream filter:\n%s", got)
	}
}

func TestResolveWindow(t *testing.T) {
	now := time.Date(2026, 1, 2, 12, 0, 0, 0, time.UTC)

	start, end, err := ResolveWindow(now, "2026-01-02T10:00:00Z", "2026-01-02T10:05:00Z", "")
	if err != nil || !start.Equal(now.Add(-2*time.Hour)) || !end.Equal(now.Add(-115*time.Minute)) {
		t.Errorf("absolute window: %v %v %v", start, end, err)
	}

	start, end, err = ResolveWindow(now, "", "", "1h")
	if err != nil || !end.Equal(now) || !start.Equal(now.Add(-time.Hour)) {
		t.Errorf("relative window: %v %v %v", start, end, err)
	}

	if _, _, err := ResolveWindow(now, "", "", "48h"); err == nil {
		t.Error("expected error for window > 24h")
	}
	if _, _, err := ResolveWindow(now, "2026-01-02T13:00:00Z", "", ""); err == nil {
		t.Error("expected error for start after end")
	}
}

func TestHandle(t *testing.T) {
	fake := &fakeLogs{rows: [][]cwTypes.ResultField{{
		{Field: aws.String("@timestamp"), Value: aws.String("2026-01-02 10:00:01.000")},
		{Field: aws.String("status"), Value: aws.String("503")},
		{Field: aws.String("flags"), Value: aws.String("UH")},
		{Field: aws.String("@ptr"), Value: aws.String("ignored")},
	}}}
	c := NewClient(fake)

	out, err := c.Handle(context.Background(),
		`{"source":"nsgw","query":"trace","match":"/api/pulp/","start_time":"2026-01-02T10:00:00Z","end_time":"2026-01-02T10:05:00Z"}`)
	if err != nil {
		t.Fatal(err)
	}
	if got := fake.input.LogGroupNames; len(got) != 1 || got[0] != "pulpp01ue1.openshift-ingress" {
		t.Errorf("default env should be prod NSGW group, got %v", got)
	}
	if !strings.Contains(out, "status=503 | flags=UH") || strings.Contains(out, "@ptr") {
		t.Errorf("unexpected output:\n%s", out)
	}

	if _, err := c.Handle(context.Background(), `{"source":"akamai","query":"raw"}`); err == nil {
		t.Error("expected error for unknown source")
	}
}

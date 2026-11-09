package anthropicClient

import "testing"

func TestVertexHost(t *testing.T) {
	cases := map[string]string{
		"global":   "aiplatform.googleapis.com",
		"":         "aiplatform.googleapis.com",
		"us-east5": "us-east5-aiplatform.googleapis.com",
	}
	for region, want := range cases {
		if got := VertexHost(region); got != want {
			t.Errorf("VertexHost(%q) = %q, want %q", region, got, want)
		}
	}
}

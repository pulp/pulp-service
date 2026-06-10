package types

import (
	"encoding/json"
	"strings"
	"time"
)

type AlertContext struct {
	Cluster   string `json:"cluster"`
	Namespace string `json:"namespace,omitempty"`
	Pod       string `json:"pod,omitempty"`
	StartsAt  string `json:"starts_at,omitempty"`
}

type ToolResponse struct {
	ResolvedParams map[string]string `json:"resolved_params"`
	Results        string            `json:"results"`
	Truncated      bool              `json:"truncated"`
}

func (response ToolResponse) ToJSON() string {
	data, err := json.Marshal(response)
	if err != nil {
		return `{"error":"failed to marshal response"}`
	}
	return string(data)
}

func DefaultTimeWindow(alertName string) time.Duration {
	if strings.Contains(alertName, "RDS") || strings.Contains(alertName, "Storage") {
		return 30 * time.Minute
	}
	if strings.Contains(alertName, "BudgetBurn") {
		return 15 * time.Minute
	}
	return 5 * time.Minute
}

func ParseAlertContext(argsJSON string) AlertContext {
	var context AlertContext
	if argsJSON != "" {
		json.Unmarshal([]byte(argsJSON), &context)
	}
	return context
}

func Truncate(text string, maxBytes int) (string, bool) {
	if len(text) <= maxBytes {
		return text, false
	}
	return text[:maxBytes] + "\n...(truncated)", true
}

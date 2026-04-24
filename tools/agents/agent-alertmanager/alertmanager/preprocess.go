// Package alertmanager provides an HTTP client for AlertManager API v2
// and alert pre-processing logic (latency filtering, burn-rate correlation).
package alertmanager

import "strings"

// AlertGroup represents a correlated group of alerts sharing a common prefix
// (e.g. multiple PulpApiError burn-rate windows) or a standalone alert.
type AlertGroup struct {
	GroupName   string   `json:"group_name"`
	Alerts     []Alert  `json:"alerts"`
	Environment string  `json:"environment"`
	Windows    []string `json:"firing_windows,omitempty"`
}

// budgetBurnPrefixes defines the alertname prefixes that should be grouped
// together as correlated burn-rate alerts.
var budgetBurnPrefixes = []string{
	"PulpApiError",
	"PulpContentError",
}

// FilterLatencyAlerts removes alerts whose alertname starts with
// "PulpApiLatency" or "PulpContentLatency". These latency alerts are
// informational and should not trigger triage workflows.
func FilterLatencyAlerts(alerts []Alert) []Alert {
	filtered := make([]Alert, 0, len(alerts))
	for _, alert := range alerts {
		alertName := alert.Labels["alertname"]
		if strings.HasPrefix(alertName, "PulpApiLatency") ||
			strings.HasPrefix(alertName, "PulpContentLatency") {
			continue
		}
		filtered = append(filtered, alert)
	}
	return filtered
}

// GroupAlerts groups budget burn alerts by prefix and environment.
// Alerts starting with a known burn-rate prefix (PulpApiError,
// PulpContentError) are grouped under that prefix. All other alerts
// are treated as standalone groups (one alert per group).
func GroupAlerts(alerts []Alert) []AlertGroup {
	// groupKey = "prefix|env" for burn-rate alerts, "alertname|env" for standalone.
	type groupEntry struct {
		groupName   string
		environment string
		alerts      []Alert
		windows     []string
	}

	// Use a slice to preserve insertion order and a map for lookup.
	groupOrder := make([]string, 0)
	groupMap := make(map[string]*groupEntry)

	for _, alert := range alerts {
		alertName := alert.Labels["alertname"]
		env := alert.Labels["env"]

		prefix := ""
		for _, burnPrefix := range budgetBurnPrefixes {
			if strings.HasPrefix(alertName, burnPrefix) {
				prefix = burnPrefix
				break
			}
		}

		var key string
		var name string
		if prefix != "" {
			key = prefix + "|" + env
			name = prefix
		} else {
			key = alertName + "|" + env
			name = alertName
		}

		existing, found := groupMap[key]
		if !found {
			existing = &groupEntry{
				groupName:   name,
				environment: env,
			}
			groupMap[key] = existing
			groupOrder = append(groupOrder, key)
		}

		existing.alerts = append(existing.alerts, alert)

		// For burn-rate groups, extract the window suffix as metadata.
		if prefix != "" {
			window := strings.TrimPrefix(alertName, prefix)
			if window != "" {
				existing.windows = append(existing.windows, window)
			}
		}
	}

	groups := make([]AlertGroup, 0, len(groupOrder))
	for _, key := range groupOrder {
		entry := groupMap[key]
		groups = append(groups, AlertGroup{
			GroupName:   entry.groupName,
			Alerts:     entry.alerts,
			Environment: entry.environment,
			Windows:    entry.windows,
		})
	}
	return groups
}

package alertmanager

import (
	"testing"
)

func TestFilterLatencyAlerts(t *testing.T) {
	alerts := []Alert{
		{Labels: map[string]string{"alertname": "PulpApiLatencyBudgetBurn"}},
		{Labels: map[string]string{"alertname": "PulpContentLatencyHigh"}},
		{Labels: map[string]string{"alertname": "PulpApiErrorBudgetBurn"}},
		{Labels: map[string]string{"alertname": "PulpCrashing"}},
	}

	filtered := FilterLatencyAlerts(alerts)

	if len(filtered) != 2 {
		t.Fatalf("expected 2 alerts after filtering, got %d", len(filtered))
	}

	for _, alert := range filtered {
		name := alert.Labels["alertname"]
		if name == "PulpApiLatencyBudgetBurn" || name == "PulpContentLatencyHigh" {
			t.Errorf("latency alert %q should have been filtered out", name)
		}
	}

	// Verify the correct alerts survived.
	expectedNames := map[string]bool{
		"PulpApiErrorBudgetBurn": true,
		"PulpCrashing":          true,
	}
	for _, alert := range filtered {
		name := alert.Labels["alertname"]
		if !expectedNames[name] {
			t.Errorf("unexpected alert %q in filtered results", name)
		}
	}
}

func TestGroupBudgetBurnAlerts(t *testing.T) {
	alerts := []Alert{
		{
			Labels: map[string]string{
				"alertname": "PulpApiErrorBudgetBurn1h",
				"env":       "prod",
			},
		},
		{
			Labels: map[string]string{
				"alertname": "PulpApiErrorBudgetBurn6h",
				"env":       "prod",
			},
		},
		{
			Labels: map[string]string{
				"alertname": "PulpApiErrorBudgetBurn3d",
				"env":       "prod",
			},
		},
		{
			Labels: map[string]string{
				"alertname": "PulpCrashing",
				"env":       "prod",
			},
		},
	}

	groups := GroupAlerts(alerts)

	if len(groups) != 2 {
		t.Fatalf("expected 2 groups, got %d", len(groups))
	}

	// Find the PulpApiError group and the standalone group.
	var apiErrorGroup *AlertGroup
	var standaloneGroup *AlertGroup
	for groupIdx := range groups {
		if groups[groupIdx].GroupName == "PulpApiError" {
			apiErrorGroup = &groups[groupIdx]
		}
		if groups[groupIdx].GroupName == "PulpCrashing" {
			standaloneGroup = &groups[groupIdx]
		}
	}

	if apiErrorGroup == nil {
		t.Fatal("expected a PulpApiError group")
	}
	if len(apiErrorGroup.Alerts) != 3 {
		t.Errorf("PulpApiError group should have 3 alerts, got %d", len(apiErrorGroup.Alerts))
	}
	if apiErrorGroup.Environment != "prod" {
		t.Errorf("PulpApiError group environment should be prod, got %q", apiErrorGroup.Environment)
	}

	if standaloneGroup == nil {
		t.Fatal("expected a PulpCrashing standalone group")
	}
	if len(standaloneGroup.Alerts) != 1 {
		t.Errorf("PulpCrashing group should have 1 alert, got %d", len(standaloneGroup.Alerts))
	}
}

func TestGroupAlerts_PreservesStandalone(t *testing.T) {
	alerts := []Alert{
		{
			Labels: map[string]string{
				"alertname": "PulpApiDown",
				"env":       "staging",
			},
		},
		{
			Labels: map[string]string{
				"alertname": "PulpCrashing",
				"env":       "staging",
			},
		},
	}

	groups := GroupAlerts(alerts)

	if len(groups) != 2 {
		t.Fatalf("expected 2 standalone groups, got %d", len(groups))
	}

	groupNames := make(map[string]bool)
	for _, group := range groups {
		groupNames[group.GroupName] = true
		if len(group.Alerts) != 1 {
			t.Errorf("standalone group %q should have 1 alert, got %d", group.GroupName, len(group.Alerts))
		}
	}

	if !groupNames["PulpApiDown"] {
		t.Error("expected a PulpApiDown group")
	}
	if !groupNames["PulpCrashing"] {
		t.Error("expected a PulpCrashing group")
	}
}

func TestGroupAlerts_CrossEnvironment(t *testing.T) {
	alerts := []Alert{
		{Labels: map[string]string{"alertname": "PulpApiErrorBudgetBurn", "env": "prod"}},
		{Labels: map[string]string{"alertname": "PulpApiErrorBudgetBurn", "env": "stage"}},
	}
	grouped := GroupAlerts(alerts)
	if len(grouped) != 2 {
		t.Fatalf("expected 2 groups (one per env), got %d", len(grouped))
	}
}

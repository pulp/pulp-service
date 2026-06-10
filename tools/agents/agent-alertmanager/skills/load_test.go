package skills

import "testing"

func TestLoadAnalysisSkills_ReturnsFourMessages(t *testing.T) {
	messages := LoadAnalysisSkills()
	if len(messages) != 4 {
		t.Errorf("LoadAnalysisSkills() returned %d messages, want 4", len(messages))
	}
}

func TestEmbeddedSkillFiles_NotEmpty(t *testing.T) {
	files := map[string]string{
		"pulpSOPs":          pulpSOPs,
		"pulpAlertRules":    pulpAlertRules,
		"prometheusMetrics": prometheusMetrics,
		"diagnosticRouting": diagnosticRouting,
	}
	for name, content := range files {
		if len(content) == 0 {
			t.Errorf("embedded skill %q is empty", name)
		}
	}
}

func TestEmbeddedSkillFiles_TotalSizeUnder60K(t *testing.T) {
	total := len(pulpSOPs) + len(pulpAlertRules) + len(prometheusMetrics) + len(diagnosticRouting)
	if total > 60000 {
		t.Errorf("total skill content = %d bytes, exceeds 60K limit", total)
	}
}

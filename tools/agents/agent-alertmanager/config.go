package main

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"
)

var clusterNameRegex = regexp.MustCompile(`^[a-z0-9][a-z0-9-]*$`)

// ClusterConfig holds the configuration for a single Alertmanager cluster.
type ClusterConfig struct {
	Name               string `json:"name"`
	URL                string `json:"url"`
	Token              string `json:"-"`
	InsecureSkipVerify bool   `json:"insecure_skip_verify,omitempty"`
	APIServerURL       string `json:"api_server_url,omitempty"`
	PrometheusURL      string `json:"prometheus_url,omitempty"`
	Namespace          string `json:"namespace,omitempty"`
}

func (cfg ClusterConfig) String() string {
	return fmt.Sprintf("{name:%s url:%s token:REDACTED}", cfg.Name, cfg.URL)
}

func (cfg ClusterConfig) GoString() string {
	return fmt.Sprintf("ClusterConfig{Name:%q, URL:%q, Token:REDACTED, InsecureSkipVerify:%t}",
		cfg.Name, cfg.URL, cfg.InsecureSkipVerify)
}

// clusterConfigJSON is the internal struct for parsing the JSON input,
// including the token field that ClusterConfig's json:"-" tag excludes.
type clusterConfigJSON struct {
	Name               string `json:"name"`
	URL                string `json:"url"`
	Token              string `json:"token"`
	InsecureSkipVerify bool   `json:"insecure_skip_verify,omitempty"`
	APIServerURL       string `json:"api_server_url,omitempty"`
	PrometheusURL      string `json:"prometheus_url,omitempty"`
	Namespace          string `json:"namespace,omitempty"`
}

func parseClusterConfigs() ([]ClusterConfig, error) {
	clustersJSON := os.Getenv("ALERTMANAGER_CLUSTERS")
	if clustersJSON != "" {
		return parseMultiCluster(clustersJSON)
	}

	alertmanagerURL := os.Getenv("ALERTMANAGER_URL")
	if alertmanagerURL == "" {
		return nil, fmt.Errorf("either ALERTMANAGER_CLUSTERS or ALERTMANAGER_URL must be set")
	}

	alertmanagerToken := os.Getenv("ALERTMANAGER_TOKEN")
	if alertmanagerToken == "" {
		return nil, fmt.Errorf("ALERTMANAGER_TOKEN is required when using ALERTMANAGER_URL")
	}

	insecureSkipVerify := os.Getenv("ALERTMANAGER_INSECURE_SKIP_VERIFY") != ""

	return []ClusterConfig{{
		Name:               "default",
		URL:                alertmanagerURL,
		Token:              alertmanagerToken,
		InsecureSkipVerify: insecureSkipVerify,
	}}, nil
}

func parseMultiCluster(clustersJSON string) ([]ClusterConfig, error) {
	var rawConfigs []clusterConfigJSON
	if err := json.Unmarshal([]byte(clustersJSON), &rawConfigs); err != nil {
		return nil, fmt.Errorf("parse ALERTMANAGER_CLUSTERS: %w", err)
	}

	if len(rawConfigs) == 0 {
		return nil, fmt.Errorf("ALERTMANAGER_CLUSTERS is empty")
	}

	seenNames := make(map[string]bool)
	configs := make([]ClusterConfig, 0, len(rawConfigs))

	for idx, raw := range rawConfigs {
		if raw.Name == "" {
			return nil, fmt.Errorf("cluster at index %d: name is required", idx)
		}
		if !clusterNameRegex.MatchString(raw.Name) {
			return nil, fmt.Errorf("cluster %q: name must match %s", raw.Name, clusterNameRegex.String())
		}
		if strings.Contains(raw.Name, "|") {
			return nil, fmt.Errorf("cluster %q: name must not contain pipe character", raw.Name)
		}
		if seenNames[raw.Name] {
			return nil, fmt.Errorf("cluster %q: duplicate name", raw.Name)
		}
		if raw.URL == "" {
			return nil, fmt.Errorf("cluster %q: url is required", raw.Name)
		}
		if raw.Token == "" {
			return nil, fmt.Errorf("cluster %q: token is required", raw.Name)
		}

		seenNames[raw.Name] = true
		configs = append(configs, ClusterConfig{
			Name:               raw.Name,
			URL:                raw.URL,
			Token:              raw.Token,
			InsecureSkipVerify: raw.InsecureSkipVerify,
			APIServerURL:       raw.APIServerURL,
			PrometheusURL:      raw.PrometheusURL,
			Namespace:          raw.Namespace,
		})
	}

	return configs, nil
}

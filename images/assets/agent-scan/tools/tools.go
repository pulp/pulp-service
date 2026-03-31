package tools

import (
	"encoding/json"
	"fmt"
	"strings"

	"agent-scan/utils"

	"github.com/tmc/langchaingo/llms"
)

// ScanTools returns the tool definitions available to the LLM.
func ScanTools() []llms.Tool {
	return []llms.Tool{
		{
			Type: "function",
			Function: &llms.FunctionDefinition{
				Name:        "list_archive_files",
				Description: "List all files inside a .tar.gz or .whl archive. Returns one file path per line.",
				Parameters: map[string]interface{}{
					"type": "object",
					"properties": map[string]interface{}{
						"archive_path": map[string]interface{}{
							"type":        "string",
							"description": "Absolute path to the .tar.gz or .whl archive",
						},
					},
					"required": []string{"archive_path"},
				},
			},
		},
		{
			Type: "function",
			Function: &llms.FunctionDefinition{
				Name:        "read_archive_file",
				Description: "Read the content of a single file inside a .tar.gz or .whl archive. Binary files return a placeholder.",
				Parameters: map[string]interface{}{
					"type": "object",
					"properties": map[string]interface{}{
						"archive_path": map[string]interface{}{
							"type":        "string",
							"description": "Absolute path to the .tar.gz or .whl archive",
						},
						"file_path": map[string]interface{}{
							"type":        "string",
							"description": "Path of the file inside the archive (as returned by list_archive_files)",
						},
					},
					"required": []string{"archive_path", "file_path"},
				},
			},
		},
	}
}

const maxContentLen = 1 << 20 // 1 MiB

// archiveCache avoids re-extracting the same archive on every tool invocation
// during a scan.
var archiveCache = utils.NewArchiveCache()

// ExecuteTool dispatches a tool call by name and returns the result string.
func ExecuteTool(name, argsJSON string) (string, error) {
	var args map[string]string
	if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
		return "", fmt.Errorf("parse tool args: %w", err)
	}

	switch name {
	case "list_archive_files":
		return listArchiveFiles(args)
	case "read_archive_file":
		return readArchiveFile(args)
	default:
		return "", fmt.Errorf("unknown tool: %s", name)
	}
}

func listArchiveFiles(args map[string]string) (string, error) {
	archivePath := args["archive_path"]
	if archivePath == "" {
		return "", fmt.Errorf("archive_path is required")
	}

	entries, err := archiveCache.Get(archivePath)
	if err != nil {
		return "", fmt.Errorf("extract %s: %w", archivePath, err)
	}

	var lines []string
	for _, e := range entries {
		marker := ""
		if e.IsBinary {
			marker = " [binary]"
		}
		lines = append(lines, e.Path+marker)
	}
	return strings.Join(lines, "\n"), nil
}

func readArchiveFile(args map[string]string) (string, error) {
	archivePath := args["archive_path"]
	filePath := args["file_path"]
	if archivePath == "" || filePath == "" {
		return "", fmt.Errorf("archive_path and file_path are required")
	}

	entries, err := archiveCache.Get(archivePath)
	if err != nil {
		return "", fmt.Errorf("extract %s: %w", archivePath, err)
	}

	for _, e := range entries {
		if e.Path == filePath {
			return utils.Truncate(e.Content, maxContentLen), nil
		}
	}
	return "", fmt.Errorf("file %s not found in archive %s", filePath, archivePath)
}

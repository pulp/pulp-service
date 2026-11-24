package main

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// publishToGitLab commits page as file in repoURL's default branch, so GitLab
// Pages serves it (the repository's CI copies it to public/). The push is
// retried once after a rebase, since prod and stage may push concurrently.
func publishToGitLab(ctx context.Context, repoURL, token, file, env string, page []byte) error {
	askPass, err := os.CreateTemp("", "git-askpass-*")
	if err != nil {
		return fmt.Errorf("create askpass script: %w", err)
	}
	defer os.Remove(askPass.Name())
	escaped := strings.ReplaceAll(token, "'", "'\\''")
	fmt.Fprintf(askPass, "#!/bin/sh\nprintf '%%s\\n' '%s'\n", escaped)
	askPass.Close()
	if err := os.Chmod(askPass.Name(), 0o700); err != nil {
		return err
	}

	cloneDir, err := os.MkdirTemp("", "task-monitor-pages-*")
	if err != nil {
		return err
	}
	defer os.RemoveAll(cloneDir)

	env0 := append(os.Environ(), "GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS="+askPass.Name())
	git := func(dir string, args ...string) error {
		cmd := exec.CommandContext(ctx, "git", args...)
		cmd.Dir = dir
		cmd.Env = env0
		cmd.Stdout = os.Stderr
		cmd.Stderr = os.Stderr
		if err := cmd.Run(); err != nil {
			return fmt.Errorf("git %s: %w", args[0], err)
		}
		return nil
	}

	url := repoURL
	if !strings.HasSuffix(url, ".git") {
		url += ".git"
	}
	url = strings.Replace(url, "https://", "https://oauth2@", 1)

	fmt.Fprintf(os.Stderr, "[gitlab] cloning %s\n", repoURL)
	if err := git("", "clone", "--depth=1", url, cloneDir); err != nil {
		return err
	}
	for _, kv := range [][2]string{{"user.name", "Pulp Task Monitor Agent"}, {"user.email", "noreply@redhat.com"}} {
		if err := git(cloneDir, "config", kv[0], kv[1]); err != nil {
			return err
		}
	}
	target := filepath.Join(cloneDir, filepath.FromSlash(file))
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return err
	}
	if err := os.WriteFile(target, page, 0o644); err != nil {
		return err
	}
	if err := git(cloneDir, "add", "--", file); err != nil {
		return err
	}
	if git(cloneDir, "diff", "--cached", "--quiet") == nil {
		fmt.Fprintf(os.Stderr, "[gitlab] %s unchanged\n", file)
		return nil
	}
	if err := git(cloneDir, "commit", "-m", "Update status page with latest "+env+" data"); err != nil {
		return err
	}
	if err := git(cloneDir, "push", "origin", "HEAD"); err != nil {
		fmt.Fprintf(os.Stderr, "[gitlab] push failed, rebasing and retrying once\n")
		if err := git(cloneDir, "pull", "--rebase", "origin", "HEAD"); err != nil {
			return err
		}
		if err := git(cloneDir, "push", "origin", "HEAD"); err != nil {
			return err
		}
	}
	fmt.Fprintf(os.Stderr, "[gitlab] published %s\n", file)
	return nil
}

package main

import (
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

type gitlabPublisher struct {
	repoURL     string
	token       string
	cloneDir    string
	askPassPath string
}

func newGitLabPublisher(repoURL, token string) *gitlabPublisher {
	return &gitlabPublisher{
		repoURL: repoURL,
		token:   token,
	}
}

func (g *gitlabPublisher) Publish(ctx context.Context, outputDir string) (string, error) {
	askPass, err := g.createAskPassScript()
	if err != nil {
		return "", fmt.Errorf("create askpass script: %w", err)
	}
	g.askPassPath = askPass

	cloneDir, err := os.MkdirTemp("", "akamai-report-repo-*")
	if err != nil {
		return "", fmt.Errorf("create temp dir: %w", err)
	}
	g.cloneDir = cloneDir

	fmt.Fprintf(os.Stderr, "[gitlab] cloning repository...\n")
	if err := g.gitCmd(ctx, "clone", "--depth=1", g.cloneURL(), cloneDir); err != nil {
		return "", fmt.Errorf("git clone: %w", err)
	}

	if err := g.gitCmdInRepo(ctx, "config", "user.name", "Agent Akamai Report"); err != nil {
		return "", fmt.Errorf("git config user.name: %w", err)
	}
	if err := g.gitCmdInRepo(ctx, "config", "user.email", "noreply@redhat.com"); err != nil {
		return "", fmt.Errorf("git config user.email: %w", err)
	}

	dateStr := time.Now().UTC().Format("2006-01-02")
	reportDir := filepath.Join(cloneDir, "public", "reports", dateStr)
	if err := os.MkdirAll(reportDir, 0o755); err != nil {
		return "", fmt.Errorf("create report dir: %w", err)
	}

	for _, name := range []string{"traffic.json", "traffic.html"} {
		if err := copyFile(filepath.Join(outputDir, name), filepath.Join(reportDir, name)); err != nil {
			return "", fmt.Errorf("copy %s: %w", name, err)
		}
	}
	fmt.Fprintf(os.Stderr, "[gitlab] copied report files to public/reports/%s/\n", dateStr)

	publicDir := filepath.Join(cloneDir, "public")
	if err := generateIndexPage(publicDir); err != nil {
		return "", fmt.Errorf("generate index page: %w", err)
	}
	fmt.Fprintf(os.Stderr, "[gitlab] generated index page\n")

	if err := g.ensureGitLabCI(); err != nil {
		return "", fmt.Errorf("ensure .gitlab-ci.yml: %w", err)
	}

	if err := g.gitCmdInRepo(ctx, "add", "-A"); err != nil {
		return "", fmt.Errorf("git add: %w", err)
	}

	if err := g.gitCmdInRepo(ctx, "diff", "--cached", "--quiet"); err == nil {
		fmt.Fprintf(os.Stderr, "[gitlab] no changes to commit (report already up to date)\n")
		return dateStr, nil
	}

	commitMsg := fmt.Sprintf("Add Akamai traffic report for %s", dateStr)
	if err := g.gitCmdInRepo(ctx, "commit", "-m", commitMsg); err != nil {
		return "", fmt.Errorf("git commit: %w", err)
	}

	fmt.Fprintf(os.Stderr, "[gitlab] pushing to repository...\n")
	if err := g.gitCmdInRepo(ctx, "push", "origin", "HEAD"); err != nil {
		return "", fmt.Errorf("git push: %w", err)
	}

	fmt.Fprintf(os.Stderr, "[gitlab] report published for %s\n", dateStr)
	return dateStr, nil
}

func (g *gitlabPublisher) Cleanup() {
	if g.askPassPath != "" {
		os.Remove(g.askPassPath)
	}
	if g.cloneDir != "" {
		os.RemoveAll(g.cloneDir)
	}
}

func (g *gitlabPublisher) createAskPassScript() (string, error) {
	f, err := os.CreateTemp("", "git-askpass-*")
	if err != nil {
		return "", err
	}
	escapedToken := strings.ReplaceAll(g.token, "'", "'\\''")
	fmt.Fprintf(f, "#!/bin/sh\nprintf '%%s\\n' '%s'\n", escapedToken)
	f.Close()
	if err := os.Chmod(f.Name(), 0o700); err != nil {
		os.Remove(f.Name())
		return "", err
	}
	return f.Name(), nil
}

func (g *gitlabPublisher) cloneURL() string {
	url := g.repoURL
	if !strings.HasSuffix(url, ".git") {
		url += ".git"
	}
	return strings.Replace(url, "https://", "https://oauth2@", 1)
}

func (g *gitlabPublisher) gitEnv() []string {
	env := append(os.Environ(), "GIT_TERMINAL_PROMPT=0")
	if g.askPassPath != "" {
		env = append(env, "GIT_ASKPASS="+g.askPassPath)
	}
	return env
}

func (g *gitlabPublisher) ensureGitLabCI() error {
	ciPath := filepath.Join(g.cloneDir, ".gitlab-ci.yml")
	if _, err := os.Stat(ciPath); err == nil {
		return nil
	}
	return os.WriteFile(ciPath, []byte(gitlabCITemplate), 0o644)
}

func (g *gitlabPublisher) gitCmd(ctx context.Context, args ...string) error {
	cmd := exec.CommandContext(ctx, "git", args...)
	cmd.Env = g.gitEnv()
	cmd.Stdout = os.Stderr
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

func (g *gitlabPublisher) gitCmdInRepo(ctx context.Context, args ...string) error {
	cmd := exec.CommandContext(ctx, "git", args...)
	cmd.Dir = g.cloneDir
	cmd.Env = g.gitEnv()
	cmd.Stdout = os.Stderr
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()

	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer out.Close()

	_, err = io.Copy(out, in)
	return err
}

const gitlabCITemplate = `pages:
  stage: deploy
  tags:
    - itup-alm-x86
  script:
    - echo "Deploying Akamai reports to GitLab Pages"
  artifacts:
    paths:
      - public
  only:
    - main
`

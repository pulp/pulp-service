package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	s3types "github.com/aws/aws-sdk-go-v2/service/s3/types"
	"github.com/aws/smithy-go"
)

// errConflict means another run changed the object since it was loaded.
var errConflict = errors.New("state was modified by another run")

// Store persists objects with optimistic concurrency: Save only succeeds if
// the object still has the version returned by Load ("" = must not exist).
type Store interface {
	Load(ctx context.Context, key string) (data []byte, version string, err error)
	Save(ctx context.Context, key string, data []byte, version string) (newVersion string, err error)
	// Put writes an object unconditionally (published artifacts).
	Put(ctx context.Context, key string, data []byte, contentType string) error
	Describe(key string) string
}

func stateKey(env string) string  { return env + "/state.json" }
func statusKey(env string) string { return env + "/index.html" }

// LoadState reads the state for env, or returns a fresh state if none exists.
func LoadState(ctx context.Context, store Store, env, baseURL string) (*State, string, error) {
	data, version, err := store.Load(ctx, stateKey(env))
	if err != nil {
		return nil, "", fmt.Errorf("load state %s: %w", store.Describe(stateKey(env)), err)
	}
	if data == nil {
		fmt.Fprintf(os.Stderr, "[state] no state at %s, starting fresh\n", store.Describe(stateKey(env)))
		return newState(env, baseURL), "", nil
	}
	st := &State{}
	if err := json.Unmarshal(data, st); err != nil {
		return nil, "", fmt.Errorf("decode state %s: %w", store.Describe(stateKey(env)), err)
	}
	if st.Version != stateVersion {
		return nil, "", fmt.Errorf("state %s has version %d, this binary supports %d", store.Describe(stateKey(env)), st.Version, stateVersion)
	}
	if st.Environment != env {
		return nil, "", fmt.Errorf("state %s belongs to environment %q, not %q", store.Describe(stateKey(env)), st.Environment, env)
	}
	st.normalize()
	return st, version, nil
}

// SaveState writes st if nobody else has written it since version was loaded.
func SaveState(ctx context.Context, store Store, st *State, version string) (string, error) {
	data, err := json.Marshal(st)
	if err != nil {
		return "", fmt.Errorf("encode state: %w", err)
	}
	newVersion, err := store.Save(ctx, stateKey(st.Environment), data, version)
	if err != nil {
		return "", fmt.Errorf("save state %s: %w", store.Describe(stateKey(st.Environment)), err)
	}
	fmt.Fprintf(os.Stderr, "[state] saved %s (%d bytes)\n", store.Describe(stateKey(st.Environment)), len(data))
	return newVersion, nil
}

// --- Local directory store (development and tests) ---

type localStore struct {
	dir string
}

func (l *localStore) path(key string) string { return filepath.Join(l.dir, filepath.FromSlash(key)) }

func (l *localStore) Describe(key string) string { return l.path(key) }

func contentVersion(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func (l *localStore) Load(_ context.Context, key string) ([]byte, string, error) {
	data, err := os.ReadFile(l.path(key))
	if errors.Is(err, os.ErrNotExist) {
		return nil, "", nil
	}
	if err != nil {
		return nil, "", err
	}
	return data, contentVersion(data), nil
}

func (l *localStore) Save(ctx context.Context, key string, data []byte, version string) (string, error) {
	current, currentVersion, err := l.Load(ctx, key)
	if err != nil {
		return "", err
	}
	if (current == nil && version != "") || (current != nil && currentVersion != version) {
		return "", errConflict
	}
	if err := l.write(key, data); err != nil {
		return "", err
	}
	return contentVersion(data), nil
}

func (l *localStore) Put(_ context.Context, key string, data []byte, _ string) error {
	return l.write(key, data)
}

func (l *localStore) write(key string, data []byte) error {
	p := l.path(key)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(p), ".tmp-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), p)
}

// --- S3 store ---

type s3Store struct {
	client *s3.Client
	bucket string
	prefix string
}

// newS3Store uses the standard AWS credential chain (AWS_ACCESS_KEY_ID,
// AWS_SECRET_ACCESS_KEY, AWS_REGION, ...). endpoint overrides the S3 endpoint
// for S3-compatible stores.
func newS3Store(ctx context.Context, bucket, prefix, endpoint string) (*s3Store, error) {
	cfg, err := config.LoadDefaultConfig(ctx)
	if err != nil {
		return nil, fmt.Errorf("load AWS config: %w", err)
	}
	if cfg.Region == "" {
		return nil, fmt.Errorf("AWS region is not set (AWS_REGION)")
	}
	client := s3.NewFromConfig(cfg, func(o *s3.Options) {
		if endpoint != "" {
			o.BaseEndpoint = aws.String(endpoint)
			o.UsePathStyle = true
		}
	})
	if prefix != "" && !strings.HasSuffix(prefix, "/") {
		prefix += "/"
	}
	return &s3Store{client: client, bucket: bucket, prefix: prefix}, nil
}

func (s *s3Store) key(key string) string { return s.prefix + key }

func (s *s3Store) Describe(key string) string { return "s3://" + s.bucket + "/" + s.key(key) }

func (s *s3Store) Load(ctx context.Context, key string) ([]byte, string, error) {
	out, err := s.client.GetObject(ctx, &s3.GetObjectInput{Bucket: aws.String(s.bucket), Key: aws.String(s.key(key))})
	if err != nil {
		var noKey *s3types.NoSuchKey
		if errors.As(err, &noKey) {
			return nil, "", nil
		}
		return nil, "", err
	}
	defer out.Body.Close()
	data, err := io.ReadAll(out.Body)
	if err != nil {
		return nil, "", err
	}
	return data, aws.ToString(out.ETag), nil
}

func (s *s3Store) Save(ctx context.Context, key string, data []byte, version string) (string, error) {
	in := &s3.PutObjectInput{
		Bucket:      aws.String(s.bucket),
		Key:         aws.String(s.key(key)),
		Body:        bytes.NewReader(data),
		ContentType: aws.String("application/json"),
	}
	if version == "" {
		in.IfNoneMatch = aws.String("*")
	} else {
		in.IfMatch = aws.String(version)
	}
	out, err := s.client.PutObject(ctx, in)
	if err != nil {
		var apiErr smithy.APIError
		if errors.As(err, &apiErr) && (apiErr.ErrorCode() == "PreconditionFailed" || apiErr.ErrorCode() == "ConditionalRequestConflict") {
			return "", errConflict
		}
		return "", err
	}
	return aws.ToString(out.ETag), nil
}

func (s *s3Store) Put(ctx context.Context, key string, data []byte, contentType string) error {
	_, err := s.client.PutObject(ctx, &s3.PutObjectInput{
		Bucket:       aws.String(s.bucket),
		Key:          aws.String(s.key(key)),
		Body:         bytes.NewReader(data),
		ContentType:  aws.String(contentType),
		CacheControl: aws.String("no-cache"),
	})
	return err
}

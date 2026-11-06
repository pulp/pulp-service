.PHONY: build build-alertmanager build-splunk tidy test test-shared test-splunk

build: build-alertmanager build-splunk

build-alertmanager:
	CGO_ENABLED=0 go build -ldflags="-s -w" -o agent-alertmanager ./agent-alertmanager

# agent-splunk is a standalone module (not in go.work), so build it with GOWORK=off.
build-splunk:
	cd agent-splunk && GOWORK=off CGO_ENABLED=0 go build -ldflags="-s -w" -o agent-splunk .

tidy:
	cd shared && go mod tidy
	cd agent-alertmanager && go mod tidy
	go work sync
	cd agent-splunk && GOWORK=off go mod tidy

test: test-shared
	go test ./agent-alertmanager/... -v

test-shared:
	go test ./shared/... -v

test-splunk:
	cd agent-splunk && GOWORK=off go test ./... -v

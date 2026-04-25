.PHONY: build build-alertmanager tidy test

build: build-alertmanager

build-alertmanager:
	CGO_ENABLED=0 go build -ldflags="-s -w" -o agent-alertmanager ./agent-alertmanager

tidy:
	cd shared && go mod tidy
	cd agent-alertmanager && go mod tidy
	go work sync

test:
	go test ./agent-alertmanager/... -v

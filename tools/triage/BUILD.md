# triage binary

Pre-built from: https://github.com/pulp/agent-project (triage/ directory)

## Rebuild

```bash
cd agent-project/triage
cargo build --release --target x86_64-unknown-linux-gnu
cp target/release/triage /path/to/pulp-service/tools/triage/triage
cd /path/to/pulp-service/tools/triage && sha256sum triage > triage.sha256
```

## Verify

```bash
cd tools/triage && sha256sum -c triage.sha256
```

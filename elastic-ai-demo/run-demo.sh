#!/usr/bin/env bash
set -euo pipefail

# Allow CI to inject real git metadata while keeping local defaults.
GITHUB_SHA="${GITHUB_SHA:-1234567890}"
GITHUB_BRANCH="${GITHUB_BRANCH:-main}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-AlexanderWert/opentelemetry-demo}"

helm upgrade --install my-otel-demo open-telemetry/opentelemetry-demo \
  -f export-to-elastic.yml \
  --set-string default.envOverrides[0].value="git\.sha=${GITHUB_SHA}\,git\.branch=${GITHUB_BRANCH}\,git\.repo=${GITHUB_REPOSITORY}"
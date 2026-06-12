# Hermes Agent Deployment Runbook

This repository uses a direct install under `/home/lighthouse/.hermes/hermes-agent`.
Keep secrets in `/home/lighthouse/.hermes/.env`; do not paste secret values into logs, issues or README files.

## Current Snapshot

- Install path: `/home/lighthouse/.hermes/hermes-agent`
- Package version: `0.8.0` from `pyproject.toml`
- Git state in the local workspace may be dirty while routing work is in progress.
- The running Gateway process must be treated separately from local source edits.

## Operational Rules

1. Do not restart the Gateway just to sync docs.
2. Do not mutate Cron while a routing investigation is in progress.
3. Use the model routing strategy doc as the source of truth for selector, health and fallback behavior.
4. Use the progress doc as the recovery log for cross-session continuation.

## Deployment Boundary

- Source changes and documentation changes are not automatically loaded into the running Gateway.
- A maintenance window is required before any rollout that changes live routing behavior.
- If the Gateway unit is missing on the host, the deployment procedure must be chosen explicitly rather than guessed.

## Useful Files

- `MODEL_ROUTING_STRATEGY.md`
- `MODEL_ROUTING_IMPLEMENTATION_PROGRESS.md`

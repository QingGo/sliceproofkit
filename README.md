# sliceproofkit (agent-kit)

A small, opinionated “control-plane” kit for code agents:
- **slice refactors**
- **fail fast**
- **evidence-driven** runs via timestamped logs

This repo contains:
- `common/`: portable templates (AGENT.md, docs/, memory/, scripts/)
- `agents/`: agent-specific rule injection (.agent/.trae/.cursorrules/etc.)
- `manifest.yaml`: copy plan
- `apply_agentkit.py`: apply the kit into any target repo

## Quick start (local kit repo)

Apply to current repo:

```bash
python3 /path/to/agent-kit/apply_agentkit.py --dest . --agents all
# or select
python3 /path/to/agent-kit/apply_agentkit.py --dest . --agents antigravity,trae
```

Run with evidence logs:

```bash
./scripts/run_with_log.sh smoke -- echo "hello"
./scripts/grep_logs.sh "hello"
```

Fast verify gate (expected to fail-fast on unknown stacks until configured):

```bash
./scripts/run_with_log.sh verify_fast -- ./scripts/verify_fast.sh
```

## Package (PyPI)

After publishing, you can use:

```bash
pip install sliceproofkit
sliceproofkit apply --dest . --agents all
# or
uvx sliceproofkit apply --dest . --agents cursor,continue,cline
```

## Adding a new agent

1) Create `agents/<agent_name>/...`
2) Add it to `manifest.yaml` under `agents:`
3) Done — the apply tool discovers agents from manifest automatically.

# Model-Backed Generation Runbook

This runbook is for raw model-backed task construction on the two pinned real-repo
profiles. It intentionally keeps human QC separate from the raw model run.

## Hosted Adapter

The model backend calls an external command. For hosted models, use:

```bash
python -m pbgen.testgen.hosted_model_adapter
```

Required environment:

```bash
export PBGEN_HOSTED_MODEL_ENDPOINT="https://provider.example/v1/chat/completions"
export PBGEN_HOSTED_MODEL_API_KEY="..."
export PBGEN_HOSTED_MODEL_NAME="review-model"
```

Optional cost metadata:

```bash
export PBGEN_HOSTED_MODEL_INPUT_COST_PER_1M="0.00"
export PBGEN_HOSTED_MODEL_OUTPUT_COST_PER_1M="0.00"
export PBGEN_HOSTED_MODEL_ROUNDS="2"
```

The adapter reads the pbgen prompt on stdin and writes only JSON with a top-level
`test_cases` array to stdout. It writes token/cost metadata to the sidecar path
provided by pbgen through `PBGEN_MODEL_METADATA_PATH`.

Some hosted reasoning models accept only the provider's default temperature.
The checked-in real-run profiles therefore record `model_temperature: 1.0`.

## Raw Model Run

Run from the real-repo workspace so artifacts remain beside the prior baselines:

```bash
cd /Users/jaylanroy/Desktop/pb/task_runs/programbench_real_repos_20260702
PATH=/Users/jaylanroy/Desktop/pb/programbench-task-generator/.venv/bin:$PATH \
  pbgen run-task --profile profiles/check_jsonschema_model_v1.pbgen_task.yaml

PATH=/Users/jaylanroy/Desktop/pb/programbench-task-generator/.venv/bin:$PATH \
  pbgen run-task --profile profiles/md4c_md2html_model_v1.pbgen_task.yaml
```

Then write the comparison report:

```bash
PATH=/Users/jaylanroy/Desktop/pb/programbench-task-generator/.venv/bin:$PATH \
  pbgen write-model-run-report \
    --artifact-pair artifacts/check_jsonschema_final artifacts/check_jsonschema_model_v1 \
    --artifact-pair artifacts/md4c_md2html_premodel_smoke_gcov artifacts/md4c_md2html_model_v1 \
    --output MODEL_RUN_REPORT.md
```

Use the raw `*_model_v1` artifacts for model-backed metrics. Any human-curated
follow-up should use new task IDs and a separate report.

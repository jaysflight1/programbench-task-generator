# Docker No-Network Evaluation

The candidate evaluator can run source submissions in a local Docker image with
networking disabled at benchmark time. Build the reusable evaluator image once:

```bash
bash scripts/build_eval_image.sh
```

This creates `pbgen-eval:py-c`, a small Debian-based runtime with Python 3,
PyYAML, bash, gcc/g++, make, and CMake. Image build may require network access
to fetch base packages, but `pbgen evaluate-submission --execution-policy
docker-no-network` runs candidates with Docker `--network none`.

Use the image for official candidate evaluation:

```bash
pbgen evaluate-submission \
  --package path/to/packages/evaluator \
  --submission-source path/to/frozen/candidate \
  --build-script path/to/frozen/candidate/build.py \
  --output-dir path/to/evaluation_docker \
  --execution-policy docker-no-network \
  --docker-image pbgen-eval:py-c
```

If Docker is unavailable or the image has not been built, the evaluator writes a
blocked `no_network_validation_report.json` rather than silently falling back to
host execution.

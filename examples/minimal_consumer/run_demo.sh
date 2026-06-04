#!/usr/bin/env bash
# run_demo.sh — run the WeatherBot consumer demo for openclaw-ontology-engine.
#
# Proves config-injection (chunk 4): the SAME engine code audits a DIFFERENT
# project (WeatherBot) purely via ONTOLOGY_CONFIG_DIR + ONTOLOGY_PROJECT_ROOT.
#
# Real consumers run `pip install openclaw-ontology-engine` first. In this repo
# we add the repo root to PYTHONPATH so `import ontology` works without install
# (equivalent to an editable install).
set -euo pipefail

DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$DEMO_DIR/../.." && pwd)"

# Layer 2 config injection — point the engine at the consumer's own YAML.
export ONTOLOGY_CONFIG_DIR="$DEMO_DIR/ontology"
export ONTOLOGY_PROJECT_ROOT="$DEMO_DIR"
# Layer 1 engine on the path (real consumers: pip install openclaw-ontology-engine).
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

echo "════════════════════════════════════════════════════════════════"
echo "  openclaw-ontology-engine · minimal consumer demo (chunk 4)"
echo "════════════════════════════════════════════════════════════════"

python3 "$DEMO_DIR/run_demo.py"
rc=$?

echo
echo "Equivalent console-script invocations (after pip install):"
echo "  ONTOLOGY_CONFIG_DIR=$DEMO_DIR/ontology \\"
echo "  ONTOLOGY_PROJECT_ROOT=$DEMO_DIR openclaw-ontology-query --json"
echo "  ONTOLOGY_CONFIG_DIR=$DEMO_DIR/ontology \\"
echo "  ONTOLOGY_PROJECT_ROOT=$DEMO_DIR openclaw-ontology-audit"
echo
echo "Note (V37.9.107 chunk-3): the convergence phase is now config-injected too —"
echo "it reads the consumer's convergence_ontology.yaml (via ONTOLOGY_CONFIG_DIR)"
echo "and resolves declared source files (e.g. weatherbot_state.json) relative to"
echo "ONTOLOGY_PROJECT_ROOT. Section 5 of the demo exercises it end-to-end. The"
echo "MRD scan-pattern parameterization (jobs_registry/notify.sh filenames) remains"
echo "a tracked follow-up (chunk-3b) — those scanners already no-op for consumers."

exit $rc

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
echo "Note: the convergence phase of openclaw-ontology-audit currently reads the"
echo "engine-bundled convergence_ontology.yaml (path not yet config-injected)."
echo "That coupling is documented in docs/ontology_engine_extension_guide.md and"
echo "is scheduled for chunk 2/3. The invariant + MRD phases ARE config-injected."

exit $rc

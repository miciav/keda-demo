#!/usr/bin/env bash
set -euo pipefail

if helm status keda -n keda &>/dev/null; then
    echo "=== Uninstalling KEDA ==="
    helm uninstall keda -n keda
    kubectl delete namespace keda --ignore-not-found
    echo "=== KEDA removed ==="
else
    echo "KEDA is not installed. Nothing to do."
fi

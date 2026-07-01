#!/usr/bin/env bash
set -euo pipefail

# Add Helm repo
helm repo add kedacore https://kedacore.github.io/charts
helm repo update

# Check if KEDA is already installed
if helm status keda -n keda 2>/dev/null; then
    echo "KEDA is already installed. Skipping."
    exit 0
fi

# Install KEDA
helm install keda kedacore/keda --namespace keda --create-namespace

# Wait for operator to be Ready
kubectl wait -n keda --for=condition=ready pod -l app=keda-operator --timeout=120s

echo "KEDA installed successfully."
exit 0

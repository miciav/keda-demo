#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="default"

echo "=== Removing ScaledObject ==="
kubectl delete scaledobject worker-scaledobject --namespace "$NAMESPACE" --ignore-not-found

echo "=== Removing Worker ==="
kubectl delete deployment worker --namespace "$NAMESPACE" --ignore-not-found

echo "=== Removing Redis ==="
kubectl delete deployment redis --namespace "$NAMESPACE" --ignore-not-found
kubectl delete service redis --namespace "$NAMESPACE" --ignore-not-found

echo "=== Cleanup done ==="

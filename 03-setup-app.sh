#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="default"

echo "=== Deploying Redis ==="
kubectl apply -f k8s/redis-deployment.yaml --namespace "$NAMESPACE"

echo "=== Deploying Worker ==="
kubectl apply -f k8s/worker-deployment.yaml --namespace "$NAMESPACE"

echo "=== Deploying ScaledObject ==="
kubectl apply -f k8s/scaledobject.yaml --namespace "$NAMESPACE"

echo "=== Waiting for Redis pod to be Ready ==="
kubectl wait --for=condition=ready pod -l app=redis --namespace "$NAMESPACE" --timeout=120s 2>/dev/null || echo "Redis pod already running or timed out, continuing..."

echo ""
echo "=== Deployment Summary ==="
echo "Redis:     $(kubectl get pods -l app=redis --namespace "$NAMESPACE" -o name 2>/dev/null || echo 'not found')"
echo "Worker:    $(kubectl get pods -l app=worker --namespace "$NAMESPACE" -o name 2>/dev/null || echo 'not found')"
echo "ScaledObject: $(kubectl get scaledobject --namespace "$NAMESPACE" -o name 2>/dev/null || echo 'not found')"
echo "=== Done ==="

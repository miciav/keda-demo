# KEDA Demo — Implementation Plan

## Architecture

```
Minikube cluster
├── Redis (1 pod) — coda FIFO (LPUSH/BRPOP)
├── Worker (deployment, 0→N) — consuma job, scaled by KEDA
├── KEDA (Helm) — ScaledObject su Redis list length
└── (Producer via TUI)

Locale
└── dashboard.py — TUI con Rich: coda, pod, log, comandi
```

## File Structure

```
keda-demo/
├── 01-setup-minikube.sh
├── 02-setup-keda.sh
├── 03-setup-app.sh
├── dashboard.py
└── k8s/
    ├── redis-deployment.yaml
    ├── worker-deployment.yaml
    └── scaledobject.yaml
```

## Global Constraints

- Shell scripts: Bash 3.x+ compatible, `set -euo pipefail`, must handle already-running state gracefully (skip, don't error)
- All kubectl commands use `--namespace default` explicitly
- Python: 3.9+ compatible, single dependency `rich` (auto-install if missing)
- KEDA ScaledObject: `minReplicaCount: 0`, `maxReplicaCount: 10`, `pollingInterval: 15`, `cooldownPeriod: 30`, `listLength: "5"`, list name `keda:queue`
- Redis: single pod, image `redis:7-alpine`, port 6379
- Worker: Python image, `replicas: 0` initially, consumes from `keda:queue` via `BRPOP`
- TUI: refresh 250ms, keyboard shortcuts 1/2/3/q, layout left (queue+events) / right (pod table + activity log)

## Task 1: Setup Minikube script

Create `01-setup-minikube.sh`.

- Check `minikube status`, if "Running" print message and exit 0
- If not running: `minikube start --cpus=2 --memory=4096`
- After start: wait for `kubectl get nodes` to show Ready, timeout 120s
- Exit 0 on success, non-zero on failure

## Task 2: Setup KEDA script

Create `02-setup-keda.sh`.

- Add Helm repo `kedacore https://kedacore.github.io/charts`
- `helm repo update`
- Check `helm status keda -n keda`, if installed print message and exit 0
- If not installed: `helm install keda kedacore/keda --namespace keda --create-namespace`
- Wait for KEDA pods to be Ready (`kubectl wait -n keda --for=condition=ready pod -l app=keda-operator --timeout=120s`)
- Exit 0 on success, non-zero on failure

## Task 3: App manifests and setup script

Create `k8s/redis-deployment.yaml`:
- Deployment, 1 replica, image `redis:7-alpine`, port 6379, Service on port 6379

Create `k8s/worker-deployment.yaml`:
- Deployment, 0 replicas, image `python:3.11-slim`
- Command: inline Python script that connects to Redis, loops `BRPOP keda:queue`, sleeps random 0.5-3s per job
- Env var `REDIS_HOST=redis` set

Create `k8s/scaledobject.yaml`:
- KEDA ScaledObject per Task constraints (Redis trigger, listName keda:queue, listLength 5, min 0, max 10, pollingInterval 15, cooldownPeriod 30)

Create `03-setup-app.sh`:
- `kubectl apply -f k8s/redis-deployment.yaml`
- `kubectl apply -f k8s/worker-deployment.yaml`
- `kubectl apply -f k8s/scaledobject.yaml`
- Wait for Redis pod Ready
- Print summary

## Task 4: TUI Dashboard

Create `dashboard.py`:
- Auto-install `rich` if missing (subprocess pip install)
- Live layout with 250ms refresh: left column (queue depth bar + scale events), right column (pod status table + activity log)
- Keyboard handler: 1 = +10 jobs, 2 = +100 jobs, 3 = drain queue, q = quit
- Metrics via subprocess: `kubectl get pods -l app=worker`, `kubectl exec deploy/redis -- redis-cli LLEN keda:queue`
- Color-coded log: green=info, yellow=scale events, blue=KEDA, magenta=queue, red=errors
- Graceful shutdown on quit

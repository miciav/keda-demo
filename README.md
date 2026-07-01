# KEDA Demo — Redis Queue Autoscaler

Teaching lab on KEDA + Minikube for the Kubernetes Lab course (graduate students).

Demonstrates event-driven autoscaling with KEDA: a Redis queue grows, KEDA detects the depth and scales workers from 0 to N. When the queue drains, it scales back down to 0.

## Architecture

```
Minikube
├── Redis (1 pod) — FIFO queue (LPUSH/BRPOP)
├── Worker (deployment, 0→N replicas) — consumes jobs
├── KEDA (Helm) — ScaledObject on Redis list length
└── Producer — TUI that injects jobs

Local (outside cluster)
└── dashboard.py — Rich-based TUI: queue, pods, log, actions
```

## Usage

```bash
./01-setup-minikube.sh    # start Minikube
./02-setup-keda.sh        # install KEDA via Helm
./03-setup-app.sh         # deploy Redis + Worker + ScaledObject
python3 dashboard.py      # launch the TUI
```

Keys: `1` +10 jobs, `2` +100 jobs, `3` drain queue, `q` quit.

## Requirements

- Minikube (or any Kubernetes cluster)
- Helm 3
- kubectl
- Python 3.9+ (single dependency: `rich`, auto-installed)

## References

- [KEDA — Kubernetes Event-driven Autoscaling](https://keda.sh)
- [KEDA Redis Lists scaler](https://keda.sh/docs/latest/scalers/redis-lists/)
- [KEDA GitHub](https://github.com/kedacore/keda)

---

Original project for educational use. KEDA + Redis patterns follow the official documentation and public examples from the KEDA project.

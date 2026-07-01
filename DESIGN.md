# KEDA Demo — Design

Laboratorio didattico su KEDA + Minikube per studenti magistrali (Kubernetes Lab).

## Obiettivo

Mostrare l'autoscaling event-driven con KEDA: una coda Redis cresce, KEDA rileva la profondità e scala i worker da 0 a N. Quando la coda si svuota, scala giù fino a 0.

## Architettura

```
Minikube
├── Redis (1 pod) — coda FIFO (LPUSH/BRPOP)
├── Worker (deployment, 0→N repliche) — consuma job
├── KEDA (Helm) — ScaledObject su Redis list length
└── Producer — script/TUI che inietta job

Locale (fuori cluster)
└── dashboard.py — TUI con Rich: coda, pod, log, comandi
```

## Componenti

| File | Ruolo | Note |
|---|---|---|
| `01-setup-minikube.sh` | Avvia Minikube se spento | Skip se già Running |
| `02-setup-keda.sh` | Helm install KEDA | Skip se già installato |
| `03-setup-app.sh` | Deploya Redis + Worker + ScaledObject | Idempotente (`kubectl apply`) |
| `dashboard.py` | TUI real-time | Auto-installa `rich` se manca |
| `k8s/redis-deployment.yaml` | Redis singolo pod | Porta 6379 |
| `k8s/worker-deployment.yaml` | Worker Python | `replicas: 0`, scala via KEDA |
| `k8s/scaledobject.yaml` | Trigger Redis su `keda:queue` | `listLength: 5`, max 10 pod |

## TUI Layout

```
┌──────────────────────────────────────────────────────┐
│  KEDA Demo — Redis Queue Autoscaler       Minikube   │
├──────────────────┬───────────────────────────────────┤
│  Queue Depth     │  Pod Status (Table)               │
│  [████░░░░] 40   │  Name, Status, CPU, Jobs done     │
│  Scale Events    │                                   │
│  Last: 0→3       │  Activity Log                     │
│  Total: 4        │  Timestamped events, color-coded  │
├──────────────────┴───────────────────────────────────┤
│  [1] +10  [2] +100  [3] Drain  [q] Quit             │
└──────────────────────────────────────────────────────┘
```

- **Refresh**: 250ms via `rich.live.Live`
- **Metriche**: `kubectl get pods` + `kubectl exec redis-pod -- redis-cli LLEN`
- **Input**: tasti 1/2/3/q mappati a funzioni
- **Dipendenze**: solo `rich` (auto-install)

## KEDA ScaledObject

```yaml
triggers:
- type: redis
  metadata:
    listName: keda:queue
    listLength: "5"
minReplicaCount: 0
maxReplicaCount: 10
pollingInterval: 15
cooldownPeriod: 30
```

Formula: `desiredReplicas = ceil(queueLength / listLength)`. Con 50 job: 10 pod.

## Worker

Loop: `BRPOP keda:queue` → `sleep(random 0.5–3s)` → ripeti. Simula lavoro reale.

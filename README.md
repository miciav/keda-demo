# KEDA Demo — Redis Queue Autoscaler

Laboratorio didattico su KEDA + Minikube per il corso Kubernetes Lab (studenti magistrali).

Mostra l'autoscaling event-driven con KEDA: una coda Redis cresce, KEDA rileva la profondità e scala i worker da 0 a N. Quando la coda si svuota, scala giù fino a 0.

## Architettura

```
Minikube
├── Redis (1 pod) — coda FIFO (LPUSH/BRPOP)
├── Worker (deployment, 0→N repliche) — consuma job
├── KEDA (Helm) — ScaledObject su Redis list length
└── Producer — TUI che inietta job

Locale (fuori cluster)
└── dashboard.py — TUI con Rich: coda, pod, log, azioni
```

## Utilizzo

```bash
./01-setup-minikube.sh    # avvia Minikube
./02-setup-keda.sh        # installa KEDA via Helm
./03-setup-app.sh         # deploya Redis + Worker + ScaledObject
python3 dashboard.py      # avvia la TUI
```

Tasti: `1` +10 job, `2` +100 job, `3` drain coda, `q` quit.

## Requisiti

- Minikube (o qualsiasi cluster Kubernetes)
- Helm 3
- kubectl
- Python 3.9+ (dipendenza unica: `rich`, auto-installata)

## Riferimenti

- [KEDA — Kubernetes Event-driven Autoscaling](https://keda.sh)
- [KEDA Redis Lists scaler](https://keda.sh/docs/latest/scalers/redis-lists/)
- [KEDA GitHub](https://github.com/kedacore/keda)

---

Progetto originale per uso didattico. I pattern KEDA + Redis seguono la documentazione ufficiale e gli esempi pubblici del progetto KEDA.

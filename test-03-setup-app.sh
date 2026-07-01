#!/usr/bin/env bash
# Test script for Task 3: App manifests and setup script
# Validates YAML files, script existence, and ScaledObject values.
set -euo pipefail

SCRIPT="03-setup-app.sh"
PASS=0
FAIL=0

pass() { PASS=$((PASS + 1)); echo "  PASS: $*"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $*"; }

SANDBOX=$(mktemp -d)
export SANDBOX
cleanup() { rm -rf "$SANDBOX"; }
trap cleanup EXIT

# ---- Test 1: YAML files exist ----
echo "=== Test 1: YAML files exist ==="
for f in k8s/redis-deployment.yaml k8s/worker-deployment.yaml k8s/scaledobject.yaml; do
    if [ -f "$f" ]; then
        pass "$f exists"
    else
        fail "$f does not exist"
    fi
done

# ---- Test 2: YAML files are valid Kubernetes manifests ----
echo "=== Test 2: YAML files are valid Kubernetes manifests ==="

# Mock kubectl for dry-run validation
cat > "$SANDBOX/kubectl" << 'MOCK'
#!/usr/bin/env bash
if [ "$1" = "apply" ] && [ "$2" = "--dry-run=client" ]; then
    # Verify the file exists before claiming it's valid
    file=""
    for arg in "$@"; do
        if [ "${arg}" = "-f" ]; then
            found_f=1
            continue
        fi
        if [ -n "${found_f:-}" ] && [ -z "$file" ]; then
            file="$arg"
            break
        fi
    done
    # Also check last arg if -f wasn't followed
    if [ -z "$file" ]; then
        for arg in "$@"; do
            case "$arg" in
                *.yaml|*.yml) file="$arg";;
            esac
        done
    fi
    if [ -n "$file" ] && [ -f "$file" ]; then
        exit 0
    fi
    exit 1
fi
echo "unexpected kubectl args: $*" >&2
exit 1
MOCK
chmod +x "$SANDBOX/kubectl"
export PATH="$SANDBOX:$PATH"

for f in k8s/redis-deployment.yaml k8s/worker-deployment.yaml k8s/scaledobject.yaml; do
    if kubectl apply --dry-run=client -f "$f" 2>/dev/null; then
        pass "kubectl dry-run validates $f"
    else
        fail "kubectl dry-run failed for $f"
    fi
done

# ---- Test 3: Setup script exists and is executable ----
echo "=== Test 3: Setup script exists and is executable ==="

if [ -f "$SCRIPT" ]; then
    pass "$SCRIPT exists"
else
    fail "$SCRIPT does not exist"
fi

if [ -x "$SCRIPT" ]; then
    pass "$SCRIPT is executable"
else
    fail "$SCRIPT is not executable"
fi

# ---- Test 4: ScaledObject has correct values ----
echo "=== Test 4: ScaledObject values ==="

SO_FILE="k8s/scaledobject.yaml"

if [ ! -f "$SO_FILE" ]; then
    fail "Cannot check ScaledObject values: $SO_FILE does not exist"
else
    # Check listLength = 5
    if grep -q 'listLength.*5' "$SO_FILE"; then
        pass "ScaledObject listLength is 5"
    else
        fail "ScaledObject listLength is not 5"
    fi

    # Check minReplicaCount = 0
    if grep -q 'minReplicaCount.*0' "$SO_FILE"; then
        pass "ScaledObject minReplicaCount is 0"
    else
        fail "ScaledObject minReplicaCount is not 0"
    fi

    # Check maxReplicaCount = 10
    if grep -q 'maxReplicaCount.*10' "$SO_FILE"; then
        pass "ScaledObject maxReplicaCount is 10"
    else
        fail "ScaledObject maxReplicaCount is not 10"
    fi

    # Check listName = keda:queue
    if grep -q 'listName.*keda:queue' "$SO_FILE"; then
        pass "ScaledObject listName is keda:queue"
    else
        fail "ScaledObject listName is not keda:queue"
    fi

    # Check pollingInterval = 15
    if grep -q 'pollingInterval.*15' "$SO_FILE"; then
        pass "ScaledObject pollingInterval is 15"
    else
        fail "ScaledObject pollingInterval is not 15"
    fi

    # Check cooldownPeriod = 30
    if grep -q 'cooldownPeriod.*30' "$SO_FILE"; then
        pass "ScaledObject cooldownPeriod is 30"
    else
        fail "ScaledObject cooldownPeriod is not 30"
    fi
fi

# ---- Test 5: Redis deployment has correct spec ----
echo "=== Test 5: Redis deployment spec ==="

R_FILE="k8s/redis-deployment.yaml"

if [ ! -f "$R_FILE" ]; then
    fail "Cannot check redis-deployment.yaml: $R_FILE does not exist"
else
    # Check image is redis:7-alpine
    if grep -q 'redis:7-alpine' "$R_FILE"; then
        pass "Redis uses image redis:7-alpine"
    else
        fail "Redis image is not redis:7-alpine"
    fi

    # Check containerPort 6379
    if grep -q 'containerPort: 6379' "$R_FILE" || grep -q 'containerPort.*6379' "$R_FILE"; then
        pass "Redis has containerPort 6379"
    else
        fail "Redis does not have containerPort 6379"
    fi

    # Check Service port 6379
    if grep -q 'port: 6379' "$R_FILE"; then
        pass "Redis Service has port 6379"
    else
        fail "Redis Service does not have port 6379"
    fi

    # Check selector app=redis
    if grep -q 'app: redis' "$R_FILE"; then
        pass "Redis has selector app=redis"
    else
        fail "Redis does not have selector app=redis"
    fi
fi

# ---- Test 6: Worker deployment has correct spec ----
echo "=== Test 6: Worker deployment spec ==="

W_FILE="k8s/worker-deployment.yaml"

if [ ! -f "$W_FILE" ]; then
    fail "Cannot check worker-deployment.yaml: $W_FILE does not exist"
else
    # Check image is python:3.11-slim
    if grep -q 'python:3.11-slim' "$W_FILE"; then
        pass "Worker uses image python:3.11-slim"
    else
        fail "Worker image is not python:3.11-slim"
    fi

    # Check replicas is 0
    if grep -q 'replicas: 0' "$W_FILE"; then
        pass "Worker has replicas: 0"
    else
        fail "Worker replicas is not 0"
    fi

    # Check REDIS_HOST env var (multi-line YAML: name/value on separate lines)
    if grep -q 'name: REDIS_HOST' "$W_FILE" && grep -q 'value: redis' "$W_FILE"; then
        pass "Worker has REDIS_HOST env var set to redis"
    else
        fail "Worker does not have REDIS_HOST env var set to redis"
    fi

    # Check BRPOP usage (Python method call, case-insensitive)
    if grep -qi 'brpop' "$W_FILE"; then
        pass "Worker uses BRPOP command"
    else
        fail "Worker does not use BRPOP command"
    fi

    # Check pip install redis
    if grep -q 'pip.*install.*redis' "$W_FILE"; then
        pass "Worker installs redis-py"
    else
        fail "Worker does not install redis-py"
    fi
fi

# ---- Test 7: Setup script has correct kubectl apply commands ----
echo "=== Test 7: Setup script kubectl commands ==="

if [ ! -f "$SCRIPT" ]; then
    fail "Cannot check $SCRIPT: file does not exist"
else
    if grep -q 'kubectl apply -f k8s/redis-deployment.yaml' "$SCRIPT"; then
        pass "Script applies redis-deployment.yaml"
    else
        fail "Script does not apply redis-deployment.yaml"
    fi

    if grep -q 'kubectl apply -f k8s/worker-deployment.yaml' "$SCRIPT"; then
        pass "Script applies worker-deployment.yaml"
    else
        fail "Script does not apply worker-deployment.yaml"
    fi

    if grep -q 'kubectl apply -f k8s/scaledobject.yaml' "$SCRIPT"; then
        pass "Script applies scaledobject.yaml"
    else
        fail "Script does not apply scaledobject.yaml"
    fi

    # Check wait for redis pod
    if grep -q 'wait.*redis\|redis.*ready\|redis.*Ready' "$SCRIPT"; then
        pass "Script waits for Redis pod to be Ready"
    else
        fail "Script does not wait for Redis pod to be Ready"
    fi

    # Check set -euo pipefail
    if head -5 "$SCRIPT" | grep -qE 'set\s+-(e[^ ]*u[^ ]*o|[^ ]*e[^ ]*u[^ ]*o|[^ ]*u[^ ]*e[^ ]*o)[^ ]*' && \
       grep -q 'pipefail' "$SCRIPT"; then
        pass "Script has set -euo pipefail (combined form)"
    else
        # Try matching separate forms
        if head -10 "$SCRIPT" | grep -q 'set\s\+-[a-z]*e[a-z]*\b' && \
           head -10 "$SCRIPT" | grep -q 'set\s\+-[a-z]*u[a-z]*\b' && \
           grep -q 'set\s\+-o\s\+pipefail' "$SCRIPT"; then
            pass "Script has set -euo pipefail"
        else
            fail "Script does not have set -euo pipefail"
        fi
    fi
fi

# ---- Summary ----
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi

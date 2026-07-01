#!/usr/bin/env bash
# Test script for 01-setup-minikube.sh
# Runs in a sandboxed environment with mocked commands.
set -euo pipefail

SCRIPT="01-setup-minikube.sh"
PASS=0
FAIL=0

pass() { PASS=$((PASS + 1)); echo "  PASS: $*"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $*"; }

# ---- Setup: create a temp dir for mocked PATH ----
SANDBOX=$(mktemp -d)
export SANDBOX
cleanup() { rm -rf "$SANDBOX"; }
trap cleanup EXIT

# ---- Test 1: Script exists and is executable ----
echo "=== Test 1: Script exists and is executable ==="
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

# ---- Test 2: "minikube already running" path ----
echo "=== Test 2: minikube already running ==="

# Mock minikube: status returns Running
cat > "$SANDBOX/minikube" << 'MOCK'
#!/usr/bin/env bash
if [ "$1" = "status" ]; then
    echo "minikube
type: Control Plane
host: Running
kubelet: Running
apiserver: Running
kubeconfig: Configured
"
    exit 0
fi
echo "unexpected minikube args: $*" >&2
exit 1
MOCK
chmod +x "$SANDBOX/minikube"

# Mock kubectl: always succeeds for get nodes
cat > "$SANDBOX/kubectl" << 'MOCK'
#!/usr/bin/env bash
echo "NAME       STATUS   ROLES    AGE   VERSION
minikube   Ready    control-plane   1m    v1.32.0"
exit 0
MOCK
chmod +x "$SANDBOX/kubectl"

export PATH="$SANDBOX:$PATH"
output=$(bash "$SCRIPT" 2>&1) || true
echo "$output" | grep -qi "already running\|already.*running\|minikube.*running" && \
    pass "Script detected minikube already running" || \
    fail "Script did not detect minikube already running (output: $output)"

# Verify minikube start was NOT called
if [ -f "$SANDBOX/minikube_start_called" ]; then
    fail "minikube start was called even though minikube was already running"
else
    pass "minikube start was NOT called when already running"
fi

# ---- Test 3: minikube NOT running -> start -> ready ----
echo "=== Test 3: minikube not running, start succeeds ==="

# Mock minikube: status returns Stopped, start succeeds
cat > "$SANDBOX/minikube" << 'MOCK'
#!/usr/bin/env bash
case "$1" in
    status)
        echo "minikube
type: None
host: Stopped
kubelet: Stopped
apiserver: Stopped
kubeconfig: Stopped
"
        exit 0
        ;;
    start)
        touch "$SANDBOX/minikube_start_called"
        ;;
    *)
        echo "unexpected minikube args: $*" >&2
        exit 1
        ;;
esac
MOCK
chmod +x "$SANDBOX/minikube"

# Mock kubectl: get nodes returns Ready
cat > "$SANDBOX/kubectl" << 'MOCK'
#!/usr/bin/env bash
echo "NAME       STATUS   ROLES    AGE   VERSION
minikube   Ready    control-plane   2m    v1.32.0"
exit 0
MOCK
chmod +x "$SANDBOX/kubectl"

rm -f "$SANDBOX/minikube_start_called"
export PATH="$SANDBOX:$PATH"
output=$(bash "$SCRIPT" 2>&1) || true
if [ -f "$SANDBOX/minikube_start_called" ]; then
    pass "minikube start was called when not running"
else
    fail "minikube start was NOT called when not running (output: $output)"
fi

# ---- Test 4: Script has set -euo pipefail ----
echo "=== Test 4: Script has set -euo pipefail ==="
if head -20 "$SCRIPT" | grep -q 'set\s\+-[a-z]*e[a-z]*\b' && \
   head -20 "$SCRIPT" | grep -q 'set\s\+-[a-z]*u[a-z]*\b' && \
   grep -q 'set\s\+-o\s\+pipefail' "$SCRIPT"; then
    pass "Script has set -euo pipefail"
elif grep -q 'set\s\+-euo\s\+pipefail' "$SCRIPT"; then
    pass "Script has set -euo pipefail (combined form)"
else
    # More robust check: scan for all three within first few lines
    line=$(head -20 "$SCRIPT")
    if echo "$line" | grep -qE 'set\s+-(e[^ ]*u[^ ]*o|[^ ]*e[^ ]*u[^ ]*o|[^ ]*u[^ ]*e[^ ]*o)[^ ]*' && \
       echo "$line" | grep -q "pipefail"; then
        pass "Script has set -euo pipefail"
    else
        fail "Script does not have set -euo pipefail"
    fi
fi

# ---- Summary ----
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi

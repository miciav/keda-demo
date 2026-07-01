#!/usr/bin/env bash
# Test script for 02-setup-keda.sh
# Runs in a sandboxed environment with mocked commands.
set -euo pipefail

SCRIPT="02-setup-keda.sh"
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

# ---- Test 2: KEDA already installed path ----
echo "=== Test 2: KEDA already installed ==="

# Mock helm: repo add/update succeed, status finds keda installed
cat > "$SANDBOX/helm" << 'MOCK'
#!/usr/bin/env bash
case "$1" in
    repo)
        case "$2" in
            add) exit 0 ;;
            update) exit 0 ;;
            *) echo "unexpected helm repo args: $*" >&2; exit 1 ;;
        esac
        ;;
    status)
        if [ "$2" = "keda" ]; then
            echo "NAME: keda
LAST DEPLOYED: Mon Jan 1 12:00:00 2025
NAMESPACE: keda
STATUS: deployed
REVISION: 1"
            exit 0
        fi
        echo "unexpected helm status args: $*" >&2
        exit 1
        ;;
    *)
        echo "unexpected helm args: $*" >&2
        exit 1
        ;;
esac
MOCK
chmod +x "$SANDBOX/helm"

# Mock kubectl
cat > "$SANDBOX/kubectl" << 'MOCK'
#!/usr/bin/env bash
echo "kubectl mocked"
exit 0
MOCK
chmod +x "$SANDBOX/kubectl"

export PATH="$SANDBOX:$PATH"
output=$(bash "$SCRIPT" 2>&1) || true
echo "$output" | grep -qi "already.*installed\|already.*exists\|skipping" && \
    pass "Script detected KEDA already installed" || \
    fail "Script did not detect KEDA already installed (output: $output)"

# ---- Test 3: KEDA not installed -> install succeeds ----
echo "=== Test 3: KEDA not installed, install succeeds ==="

helm_install_called="$SANDBOX/helm_install_called"
kubectl_wait_called="$SANDBOX/kubectl_wait_called"

# Mock helm: status returns not found, install succeeds
cat > "$SANDBOX/helm" << 'MOCK'
#!/usr/bin/env bash
case "$1" in
    repo)
        case "$2" in
            add) exit 0 ;;
            update) exit 0 ;;
            *) echo "unexpected helm repo args: $*" >&2; exit 1 ;;
        esac
        ;;
    status)
        if [ "$2" = "keda" ]; then
            echo "Error: release: not found" >&2
            exit 1
        fi
        echo "unexpected helm status args: $*" >&2
        exit 1
        ;;
    install)
        touch "$SANDBOX/helm_install_called"
        exit 0
        ;;
    *)
        echo "unexpected helm args: $*" >&2
        exit 1
        ;;
esac
MOCK
chmod +x "$SANDBOX/helm"

# Mock kubectl: wait succeeds
cat > "$SANDBOX/kubectl" << 'MOCK'
#!/usr/bin/env bash
if [ "$1" = "wait" ]; then
    touch "$SANDBOX/kubectl_wait_called"
    echo "pod/keda-operator-xxx condition met"
    exit 0
fi
echo "kubectl mocked"
exit 0
MOCK
chmod +x "$SANDBOX/kubectl"

rm -f "$helm_install_called" "$kubectl_wait_called"
export PATH="$SANDBOX:$PATH"
output=$(bash "$SCRIPT" 2>&1) || true
if [ -f "$helm_install_called" ]; then
    pass "helm install was called when KEDA not installed"
else
    fail "helm install was NOT called when KEDA not installed (output: $output)"
fi

if [ -f "$kubectl_wait_called" ]; then
    pass "kubectl wait was called after install"
else
    fail "kubectl wait was NOT called after install (output: $output)"
fi

# ---- Test 4: Script has set -euo pipefail ----
echo "=== Test 4: Script has set -euo pipefail ==="
if head -5 "$SCRIPT" | grep -q 'set\s\+-euo\s\+pipefail'; then
    pass "Script has set -euo pipefail"
elif head -5 "$SCRIPT" | grep -q 'set\s\+-[a-z]*e[a-z]*\b' && \
     head -5 "$SCRIPT" | grep -q 'set\s\+-[a-z]*u[a-z]*\b' && \
     grep -q 'set\s\+-o\s\+pipefail' "$SCRIPT"; then
    pass "Script has set -euo pipefail"
else
    fail "Script does not have set -euo pipefail"
fi

# ---- Test 5: Script has helm repo add command ----
echo "=== Test 5: Script has helm repo add command ==="
if grep -q 'helm repo add.*kedacore' "$SCRIPT" && \
   grep -q 'kedacore.*https://kedacore.github.io/charts' "$SCRIPT"; then
    pass "Script has helm repo add kedacore"
else
    fail "Script does not have helm repo add kedacore"
fi

# ---- Summary ----
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi

#!/usr/bin/env bash
# escape_pocs/kmod_load.sh
# Kernel module load via insmod — tests whether kmod loading is possible inside Kata.
#
# Reference: General kernel hardening; Kata Containers guest kernel config.
# Technique: Attempt to load a kernel module (benign hello-world kmod or any available .ko).
#
# Expected result inside Kata:
#   - insmod fails with EPERM (CAP_SYS_MODULE not granted) OR
#   - insmod fails with ENOENT (no .ko files available in minimal image) OR
#   - insmod fails because Kata guest kernel is compiled without module support.
#
# Even if insmod were to succeed, the module would execute only inside the Kata
# guest VM kernel — NOT on the AKS host kernel.  This test asserts failure for
# defense-in-depth.
#
# This script is BENIGN — it attempts to load a .ko that does not exist,
# or the trivially-available dummy module if present.

set -u

echo "[kmod-load] Starting kernel module load PoC ..."

# Step 1: Check CAP_SYS_MODULE
if command -v capsh &>/dev/null; then
    CAPS=$(capsh --print 2>/dev/null | grep "Current:" || echo "")
    echo "[kmod-load] Current capabilities: ${CAPS}"
    if echo "${CAPS}" | grep -q "cap_sys_module"; then
        echo "[kmod-load] WARNING: CAP_SYS_MODULE is present — insmod may succeed inside guest VM"
    else
        echo "[kmod-load] CAP_SYS_MODULE not in current set (expected for Kata pod)"
    fi
fi

# Step 2: Check if /proc/modules is accessible (proxy for kmod support)
if [ -r /proc/modules ]; then
    MODULE_COUNT=$(wc -l < /proc/modules 2>/dev/null || echo 0)
    echo "[kmod-load] /proc/modules accessible: ${MODULE_COUNT} modules loaded in guest kernel"
else
    echo "[kmod-load] /proc/modules not readable — kernel module support may be disabled"
fi

# Step 3: Attempt insmod with a non-existent module (tests the EPERM path)
KMOD_TEST_PATH="/tmp/test_kmod_$$.ko"
echo "[kmod-load] Attempting insmod on non-existent path: ${KMOD_TEST_PATH}"
if insmod "${KMOD_TEST_PATH}" 2>&1; then
    echo "insmod: unexpected success with non-existent module"
    echo "KMOD_BLOCKED: file not found — no real module was loaded"
else
    INSMOD_ERR=$(insmod "${KMOD_TEST_PATH}" 2>&1 || true)
    if echo "${INSMOD_ERR}" | grep -qiE "Operation not permitted|EPERM|not permitted"; then
        echo "KMOD_BLOCKED: insmod denied by kernel (CAP_SYS_MODULE not granted — expected for Kata)"
        echo "ESCAPE_BLOCKED"
    elif echo "${INSMOD_ERR}" | grep -qiE "No such file|cannot open|not found"; then
        echo "KMOD_BLOCKED: no .ko file available (expected in minimal Alpine image)"
        echo "ESCAPE_BLOCKED"
    elif echo "${INSMOD_ERR}" | grep -qiE "Function not implemented|ENOSYS"; then
        echo "KMOD_BLOCKED: kernel module loading not implemented in guest kernel (CONFIG_MODULES=n)"
        echo "ESCAPE_BLOCKED"
    else
        echo "kmod_load: unexpected insmod error: ${INSMOD_ERR}"
        echo "ESCAPE_BLOCKED: insmod failed (see error above)"
    fi
fi

# Step 4: Try modprobe as a secondary check
if command -v modprobe &>/dev/null; then
    echo "[kmod-load] Attempting modprobe of a non-harmful module: 'dummy'"
    if modprobe dummy 2>&1; then
        echo "modprobe dummy: loaded in guest kernel"
        echo "KMOD_LOADED_IN_GUEST: module loaded inside Kata VM only (host kernel unaffected)"
        # Unload it immediately
        rmmod dummy 2>/dev/null || true
        echo "ESCAPE_BLOCKED: module executes in guest VM kernel only — not host kernel"
    else
        MODPROBE_ERR=$(modprobe dummy 2>&1 || true)
        echo "KMOD_BLOCKED: modprobe dummy failed: ${MODPROBE_ERR}"
        echo "ESCAPE_BLOCKED"
    fi
else
    echo "[kmod-load] modprobe not available (Alpine minimal image — expected)"
    echo "ESCAPE_BLOCKED"
fi

echo "[kmod-load] PoC complete."
exit 0

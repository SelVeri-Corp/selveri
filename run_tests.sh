#!/usr/bin/env bash
# SelVerifier Test Runner
# Runs all pass and fail tests and reports results.

set -uo pipefail

PASS_DIR="examples/verifier_tests/pass_test"
FAIL_DIR="examples/verifier_tests/fail_tests"
SELVERI_LP_PASS_DIR="examples/selveri_lp_tests/pass_test"
SELVERI_LP_FAIL_DIR="examples/selveri_lp_tests/fail_tests"
EXAMPLES_DIR="examples"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
RESET='\033[0m'

passed=0
failed=0
skipped=0
total=0
failures=()

echo -e "${BOLD}══════════════════════════════════════════${RESET}"
echo -e "${BOLD}       SelVerifier Test Suite${RESET}"
echo -e "${BOLD}══════════════════════════════════════════${RESET}"
echo

# ── Examples ────────────────────────────────────
echo -e "${BOLD}▶ Examples${RESET} (expected: compile and run without error)"
echo -e "  Directory: ${EXAMPLES_DIR}"
echo

for f in "$EXAMPLES_DIR"/*.svi; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    total=$((total + 1))

    if [ "$name" = "example_io.svi" ]; then
        output=$(echo -e "5\n3.14" | selveri "$f" 2>&1)
    else
        output=$(selveri "$f" 2>&1)
    fi
    exit_code=$?

    if [ $exit_code -eq 0 ]; then
        steps=$(echo "$output" | grep -oP 'Steps:\s*\K\d+' || echo "?")
        echo -e "  ${GREEN}✓${RESET} ${name}  (${steps} steps)"
        passed=$((passed + 1))
    else
        echo -e "  ${RED}✗${RESET} ${name}"
        echo "$output" | tail -1 | sed 's/^/      /'
        failed=$((failed + 1))
        failures+=("EXAMPLE  $name")
    fi
done

echo

# ── Pass Tests ──────────────────────────────────
echo -e "${BOLD}▶ Pass Tests${RESET} (expected: run without error)"
echo -e "  Directory: ${PASS_DIR}"
echo

for f in "$PASS_DIR"/*.svi; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    total=$((total + 1))

    output=$(selveri "$f" 2>&1)
    exit_code=$?

    if [ $exit_code -eq 0 ]; then
        steps=$(echo "$output" | grep -oP 'Steps:\s*\K\d+' || echo "?")
        echo -e "  ${GREEN}✓${RESET} ${name}  (${steps} steps)"
        passed=$((passed + 1))
    else
        echo -e "  ${RED}✗${RESET} ${name}"
        echo "$output" | tail -1 | sed 's/^/      /'
        failed=$((failed + 1))
        failures+=("PASS  $name")
    fi
done

echo

# ── Fail Tests ──────────────────────────────────
echo -e "${BOLD}▶ Fail Tests${RESET} (expected: VerificationError)"
echo -e "  Directory: ${FAIL_DIR}"
echo

for f in "$FAIL_DIR"/*.svi; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    total=$((total + 1))

    output=$(selveri "$f" 2>&1)
    exit_code=$?

    if echo "$output" | grep -qE "VerificationError|IRRuntimeError"; then
        spec=$(echo "$output" | grep -oP 'Specification #\K\d+' || echo "?")
        error_type=$(echo "$output" | grep -oP '(VerificationError|IRRuntimeError)' | head -1)
        echo -e "  ${GREEN}✓${RESET} ${name}  (${error_type}, spec #${spec})"
        passed=$((passed + 1))
    elif [ $exit_code -eq 0 ]; then
        # Check if the file has any active (uncommented) specs
        active_specs=$(grep -cP '^\s*\{' "$f" || true)
        if [ "$active_specs" -eq 0 ]; then
            echo -e "  ${YELLOW}⊘${RESET} ${name}  (no active specs — skipped)"
            skipped=$((skipped + 1))
        else
            echo -e "  ${RED}✗${RESET} ${name}  (unexpected pass)"
            failed=$((failed + 1))
            failures+=("FAIL  $name  (should have raised VerificationError)")
        fi
    else
        echo -e "  ${RED}✗${RESET} ${name}  (crashed with non-verification error)"
        echo "$output" | tail -1 | sed 's/^/      /'
        failed=$((failed + 1))
        failures+=("FAIL  $name  (unexpected crash)")
    fi
done

echo

# ── SelVeri LP Pass Tests ─────────────────────────
echo -e "${BOLD}▶ SelVeri LP Pass Tests${RESET} (expected: run without error)"
echo -e "  Directory: ${SELVERI_LP_PASS_DIR}"
echo

for f in "$SELVERI_LP_PASS_DIR"/*.svi; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    total=$((total + 1))

    output=$(selveri "$f" 2>&1)
    exit_code=$?

    if [ $exit_code -eq 0 ]; then
        steps=$(echo "$output" | grep -oP 'Steps:\s*\K\d+' || echo "?")
        echo -e "  ${GREEN}✓${RESET} ${name}  (${steps} steps)"
        passed=$((passed + 1))
    else
        echo -e "  ${RED}✗${RESET} ${name}"
        echo "$output" | tail -1 | sed 's/^/      /'
        failed=$((failed + 1))
        failures+=("SELVERI_LP_PASS  $name")
    fi
done

echo

# ── SelVeri LP Fail Tests ─────────────────────────
echo -e "${BOLD}▶ SelVeri LP Fail Tests${RESET} (expected: VerificationError or IRRuntimeError)"
echo -e "  Directory: ${SELVERI_LP_FAIL_DIR}"
echo

for f in "$SELVERI_LP_FAIL_DIR"/*.svi; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    total=$((total + 1))

    output=$(selveri "$f" 2>&1)
    exit_code=$?

    if echo "$output" | grep -qE "VerificationError|IRRuntimeError"; then
        spec=$(echo "$output" | grep -oP 'Specification #\K\d+' || echo "?")
        error_type=$(echo "$output" | grep -oP '(VerificationError|IRRuntimeError)' | head -1)
        echo -e "  ${GREEN}✓${RESET} ${name}  (${error_type}, spec #${spec})"
        passed=$((passed + 1))
    elif [ $exit_code -eq 0 ]; then
        active_specs=$(grep -cP '^\s*\{' "$f" || true)
        if [ "$active_specs" -eq 0 ]; then
            echo -e "  ${YELLOW}⊘${RESET} ${name}  (no active specs — skipped)"
            skipped=$((skipped + 1))
        else
            echo -e "  ${RED}✗${RESET} ${name}  (unexpected pass)"
            failed=$((failed + 1))
            failures+=("SELVERI_LP_FAIL  $name  (should have raised error)")
        fi
    else
        echo -e "  ${RED}✗${RESET} ${name}  (crashed with non-verification error)"
        echo "$output" | tail -1 | sed 's/^/      /'
        failed=$((failed + 1))
        failures+=("SELVERI_LP_FAIL  $name  (unexpected crash)")
    fi
done

echo
echo -e "${BOLD}══════════════════════════════════════════${RESET}"

# ── Summary ─────────────────────────────────────
if [ $failed -eq 0 ]; then
    echo -e "  ${GREEN}${BOLD}All tests passed!${RESET}"
else
    echo -e "  ${RED}${BOLD}Some tests failed.${RESET}"
fi

echo -e "  Total: ${total}  ${GREEN}Passed: ${passed}${RESET}  ${RED}Failed: ${failed}${RESET}  ${YELLOW}Skipped: ${skipped}${RESET}"

if [ ${#failures[@]} -gt 0 ]; then
    echo
    echo -e "  ${RED}Failures:${RESET}"
    for f in "${failures[@]}"; do
        echo -e "    • $f"
    done
fi

echo -e "${BOLD}══════════════════════════════════════════${RESET}"

exit $failed

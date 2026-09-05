#!/bin/bash
# Commit Preflight Check
# Must be run before git commit to verify tests pass.
# Creates a one-time token that the require-preflight.py hook validates.
#
# Usage:
#   ./scripts/commit-preflight.sh              # Full verification
#   ./scripts/commit-preflight.sh --docs-only  # Skip tests for docs changes
#   ./scripts/commit-preflight.sh --skip-tests "reason"  # Skip with reason
#   ./scripts/commit-preflight.sh --auto       # Auto-detect if tests needed
#
# Installed by /harden-repo
# Lint/test commands customized for this project during installation.

set -e

# Project-scoped token path (must match require-preflight.py)
# TRUST NOTE: this script executes code from the working tree (the test suite,
# and scripts/pre-commit.sh). Running it on a checkout you have not reviewed
# runs that branch's code on your machine. That is inherent to testing before
# committing, not a defect — but do not run preflight on an unreviewed branch.

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_KEY="$(git -C "$PROJECT_DIR" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
if [ -n "$PROJECT_KEY" ] && [ -d "$PROJECT_KEY" ]; then
    PROJECT_KEY="$(cd "$PROJECT_KEY" && pwd -P)"
else
    PROJECT_KEY="$(realpath "$PROJECT_DIR")"
fi
PROJECT_HASH=$(python3 -c "import hashlib, sys; print(hashlib.md5(sys.argv[1].encode()).hexdigest()[:8])" "$PROJECT_KEY")
TOKEN_FILE="/tmp/.preflight-token-${PROJECT_HASH}"
TOKEN_EXPIRY_SECONDS=300  # Token valid for 5 minutes

# Parse arguments
SKIP_TESTS=false
SKIP_REASON=""
AUTO_DETECT=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --docs-only)
            SKIP_TESTS=true
            SKIP_REASON="documentation-only changes"
            shift
            ;;
        --skip-tests)
            SKIP_TESTS=true
            SKIP_REASON="$2"
            shift 2
            ;;
        --auto)
            AUTO_DETECT=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--docs-only | --skip-tests \"reason\" | --auto]"
            exit 1
            ;;
    esac
done

echo "🔍 Running commit preflight checks..."
echo ""

# Get staged files
STAGED_FILES=$(git diff --cached --name-only 2>/dev/null || echo "")

if [ -z "$STAGED_FILES" ]; then
    echo "⚠️  No staged files. Stage files first with 'git add'"
    exit 1
fi

echo "📁 Staged files:"
echo "$STAGED_FILES" | head -10
TOTAL=$(echo "$STAGED_FILES" | wc -l | tr -d ' ')
if [ "$TOTAL" -gt 10 ]; then
    echo "   ... and $((TOTAL - 10)) more"
fi
echo ""

# Auto-detect if tests are needed
if [ "$AUTO_DETECT" = true ]; then
    NON_DOC_FILES=$(echo "$STAGED_FILES" | grep -vE '\.(md|txt|json|yaml|yml)$|^docs/|^specs/|^\.claude/|^README|^LICENSE|^\.gitignore' || true)
    if [ -z "$NON_DOC_FILES" ]; then
        echo "📄 Auto-detected: Documentation/config changes only"
        SKIP_TESTS=true
        SKIP_REASON="auto-detected docs/config only"
    else
        echo "🔧 Auto-detected: Code changes present - running tests"
    fi
    echo ""
fi

# Handle skip tests mode
if [ "$SKIP_TESTS" = true ]; then
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "⏭️  SKIPPING TESTS"
    echo "   Reason: $SKIP_REASON"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    TIMESTAMP=$(date +%s)
    TOKEN_DATA=$(cat <<EOF
{
    "created": $TIMESTAMP,
    "expires": $((TIMESTAMP + TOKEN_EXPIRY_SECONDS)),
    "staged_files": $(echo "$STAGED_FILES" | wc -l | tr -d ' '),
    "checks_run": "skipped",
    "skip_reason": "$SKIP_REASON"
}
EOF
)
    echo "$TOKEN_DATA" > "$TOKEN_FILE"

    echo "✅ PREFLIGHT PASSED (tests skipped)"
    echo "📝 You may now run: git commit -m \"your message\""
    echo ""
    exit 0
fi

# Track what we checked
CHECKS_RUN=""
CHECKS_PASSED=true

# ── Secret scanning (always runs) ────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔐 Running secret scan..."
if (cd "$PROJECT_DIR" && ./scripts/pre-commit.sh); then
    CHECKS_RUN="${CHECKS_RUN}secrets,"
else
    echo "❌ Secret scan failed"
    CHECKS_PASSED=false
fi

# ══════════════════════════════════════════════════════════════
# LINT SECTION — customized by /harden-repo during installation
# ══════════════════════════════════════════════════════════════
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📋 Running lint checks..."

# __HARDEN_LINT_START__
echo "⏭️  No linter detected — skipping lint"
# __HARDEN_LINT_END__

# ══════════════════════════════════════════════════════════════
# TEST SECTION — customized by /harden-repo during installation
# ══════════════════════════════════════════════════════════════
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🧪 Running tests..."

# __HARDEN_TEST_START__
# This repo's suite is tests/run.sh (pytest under the hood). Run it, and fail
# the preflight when it fails. Without this the section printed "no test runner
# detected" and the preflight reported PASSED having checked only for secrets —
# a gate that reports success without performing the check.
#
# Two deliberate choices:
#
#   - A MISSING tests/run.sh fails rather than skips. This repo has a suite, so
#     its absence means a bad checkout or a deleted runner, not a repo without
#     tests. Skipping there would recreate the same silent gate one level down:
#     delete the runner and every commit passes again.
#   - Paths are anchored to $PROJECT_DIR rather than the caller's cwd, so the
#     runner cannot be resolved out of a different tree when preflight is
#     invoked from a subdirectory or with an unexpected working directory.
if [ -f "$PROJECT_DIR/tests/run.sh" ]; then
  if (cd "$PROJECT_DIR" && bash tests/run.sh); then
    CHECKS_RUN="${CHECKS_RUN}tests,"
  else
    echo "❌ Tests failed"
    CHECKS_PASSED=false
  fi
else
  echo "❌ $PROJECT_DIR/tests/run.sh not found — refusing to attest tests"
  CHECKS_PASSED=false
fi
# __HARDEN_TEST_END__

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ "$CHECKS_PASSED" = false ]; then
    echo "❌ PREFLIGHT FAILED - Fix errors before committing"
    rm -f "$TOKEN_FILE"
    exit 1
fi

# Create confirmation token
TIMESTAMP=$(date +%s)
TOKEN_DATA=$(cat <<EOF
{
    "created": $TIMESTAMP,
    "expires": $((TIMESTAMP + TOKEN_EXPIRY_SECONDS)),
    "staged_files": $(echo "$STAGED_FILES" | wc -l | tr -d ' '),
    "checks_run": "${CHECKS_RUN%,}"
}
EOF
)

echo "$TOKEN_DATA" > "$TOKEN_FILE"

echo ""
echo "✅ PREFLIGHT PASSED"
echo ""
echo "Token created (expires in ${TOKEN_EXPIRY_SECONDS}s)"
echo "📝 You may now run: git commit -m \"your message\""
echo ""

#!/usr/bin/env bash
# Run every examples/live_*.py concurrently and print PASS / SKIP / FAIL per script
# (exit 0 / 77 / other from each script).
# Exits non-zero only on FAIL. See examples/README.md ("Live smoke tests").
#
# Provider keys usually live in ~/.zshrc, which a non-interactive shell (an agent's
# tool shell, CI) never sources — so a key-gated script would silently SKIP. When zsh
# is installed we re-exec under `zsh -ic` to pick them up. Set
# COOLHAND_EXAMPLES_NO_ZSH=1 to opt out. (-i can print zsh startup noise on a non-tty.)
set -uo pipefail

if [ -z "${COOLHAND_EXAMPLES_NO_ZSH:-}" ] && [ -z "${COOLHAND_EXAMPLES_IN_ZSH:-}" ] \
  && command -v zsh >/dev/null 2>&1; then
  export COOLHAND_EXAMPLES_IN_ZSH=1 # guard: the re-exec'd copy must not re-exec again
  # Absolute path: an rc file that cd's (autojump, direnv) must not break the re-exec.
  self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  exec zsh -ic 'exec bash "$0" "$@"' "$self" "$@"
fi

# A relative log dir is relative to the caller's cwd, so resolve it before the cd.
if [ -n "${COOLHAND_EXAMPLES_LOG_DIR:-}" ]; then
  mkdir -p "$COOLHAND_EXAMPLES_LOG_DIR" 2>/dev/null || true
  COOLHAND_EXAMPLES_LOG_DIR="$(cd "$COOLHAND_EXAMPLES_LOG_DIR" 2>/dev/null && pwd)"
fi

cd "$(dirname "$0")/.." || exit 1

scripts=(examples/live_*.py)
log_dir="${COOLHAND_EXAMPLES_LOG_DIR:-.context/live-examples}"
mkdir -p "$log_dir" 2>/dev/null || log_dir="$(mktemp -d)"

pids=()
names=()
# Ctrl-C must not leave the backgrounded provider calls running.
trap 'kill ${pids[@]+"${pids[@]}"} 2>/dev/null; exit 130' INT TERM
for script in "${scripts[@]}"; do
  name="$(basename "$script" .py)"
  names+=("$name")
  # The provider SDKs aren't project dependencies; --with keeps pyproject/uv.lock clean.
  uv run --with openai --with anthropic python "$script" >"$log_dir/$name.log" 2>&1 &
  pids+=($!)
done

failed=0
passed=0
skipped=0
for i in "${!names[@]}"; do
  name="${names[$i]}"
  log="$log_dir/$name.log"
  wait "${pids[$i]}"
  status=$?
  # Classified by exit status alone (0 pass, 77 skip, anything else fail); log text
  # never decides the verdict. Keep 77 in sync with examples/_live_check.py.
  if [ "$status" -eq 0 ]; then
    echo "PASS  $name"
    passed=$((passed + 1))
  elif [ "$status" -eq 77 ]; then
    echo "SKIP  $name  ($(grep -m1 '^Skipping' "$log" || echo 'skipped'))"
    skipped=$((skipped + 1))
  else
    echo "FAIL  $name  (exit $status, log: $log)"
    tail -n 15 "$log" | sed 's/^/      /'
    failed=$((failed + 1))
  fi
done

echo "live-examples: $passed passed, $skipped skipped, $failed failed (logs in $log_dir)"
if [ "$failed" -eq 0 ] && [ "$passed" -eq 0 ]; then
  echo "WARNING: every script was skipped — nothing was exercised against a real provider."
fi
[ "$failed" -eq 0 ]

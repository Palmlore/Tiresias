#!/usr/bin/env bash
# Scan tracked files for likely secrets (pre-submission self-check §11).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
status=0
if git ls-files | grep -E '(^|/)\.env$|\.pem$|\.key$|credentials|secrets/' ; then
  echo "FAIL: secret-looking files are tracked"; status=1
fi
if git ls-files -z | xargs -0 grep -InE '(sk-[A-Za-z0-9]{20,}|nvapi-[A-Za-z0-9_-]{20,}|BEGIN (RSA|OPENSSH) PRIVATE KEY|AKIA[0-9A-Z]{16})' ; then
  echo "FAIL: secret-looking values found"; status=1
fi
[ $status -eq 0 ] && echo "PASS: no secrets found in tracked files"
exit $status

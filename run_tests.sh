#!/usr/bin/env bash
# All tests. Extraction tests need no network; the browser tests need
# playwright + chromium (they skip cleanly if it isn't installed).
set -u
cd "$(dirname "$0")"
fail=0
for t in scraper/tests/test_extractors.py scraper/tests/test_pagination.py frontend/tests/test_app.py; do
  echo "════════ $t"
  python3 "$t" || fail=1
done
echo
[ $fail -eq 0 ] && echo "ALL SUITES PASSED" || echo "SOME SUITES FAILED"
exit $fail

#!/bin/sh
# Run coding-agent bootstrap setup, then exec the container command.
# GitLab CI passes `sh -c '<script>'` as CMD; docker run often passes `--cr-id` only.
set -e
python3 /usr/local/bin/bootstrap.py

if [ $# -eq 0 ]; then
  set -- code-review-bot --help
fi

case "$1" in
  code-review-bot|sh|/bin/sh|/usr/bin/sh|bash|/bin/bash|/usr/bin/bash)
    exec "$@"
    ;;
  *)
    exec code-review-bot "$@"
    ;;
esac

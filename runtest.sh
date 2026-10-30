#!/bin/sh
# Run pytest with a clean environment (the parent env has empty HARNESS_* vars).
exec env -i HOME=/Users/savenok PATH=/usr/bin:/bin:/opt/homebrew/bin GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t /Users/savenok/python/experiments/agent-harness/.venv/bin/python -m pytest -q "$@"

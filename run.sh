# run.sh
#!/bin/bash
set -eu

exec docker compose up --build "$@"

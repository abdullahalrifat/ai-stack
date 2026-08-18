#!/bin/sh
set -eu

python /app/render_config.py \
  --input /app/config.yaml \
  --output /tmp/litellm-config.yaml

exec litellm --config=/tmp/litellm-config.yaml "$@"

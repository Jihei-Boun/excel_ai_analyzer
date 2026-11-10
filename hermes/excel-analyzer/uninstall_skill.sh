#!/bin/bash
# excel-analyzer skill 디렉터리만 제거한다.
set -euo pipefail

if [[ -z "${HERMES_HOME:-}" ]]; then
  echo "HERMES_HOME이 필요하다." >&2
  exit 2
fi

dest="$HERMES_HOME/skills/excel-analyzer"
if [[ ! -d "$dest" ]]; then
  echo "설치된 skill이 없다: $dest" >&2
  exit 0
fi
rm -rf "$dest"
echo "removed $dest"

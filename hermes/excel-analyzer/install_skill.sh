#!/bin/bash
# 이 프로필의 excel-analyzer skill만 다시 설치한다. 다른 skill은 건드리지 않는다.
set -euo pipefail

if [[ -z "${HERMES_HOME:-}" ]]; then
  echo "HERMES_HOME이 필요하다." >&2
  exit 2
fi

source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
dest="$HERMES_HOME/skills/excel-analyzer"
mkdir -p "$HERMES_HOME/skills"
rm -rf "$dest"
mkdir -p "$dest"
cp "$source_dir/SKILL.md" "$source_dir/connect.py" "$source_dir/operator.example.json" "$source_dir/README.md" "$dest/"
chmod 755 "$dest/connect.py"
echo "installed $dest"

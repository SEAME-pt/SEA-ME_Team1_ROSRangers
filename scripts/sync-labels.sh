#!/usr/bin/env bash
# Create or update the labels in .github/labels.yml.
# Needs: gh (authenticated, any version) and yq (mikefarah, v4).
# Usage: scripts/sync-labels.sh [owner/repo]
set -euo pipefail

repo="${1:-SEAME-pt/SEA-ME_Team1_ROSRangers}"
file="$(dirname "$0")/../.github/labels.yml"

count="$(yq 'length' "$file")"
for ((i = 0; i < count; i++)); do
  name="$(yq ".[$i].name" "$file")"
  color="$(yq ".[$i].color" "$file")"
  desc="$(yq ".[$i].description" "$file")"
  encoded="$(NAME="$name" yq -n 'strenv(NAME) | @uri' | sed 's/+/%20/g')"

  # Update the label if it exists, otherwise create it.
  if gh api -X PATCH "repos/$repo/labels/$encoded" \
    -f new_name="$name" -f color="$color" -f description="$desc" >/dev/null 2>&1; then
    echo "updated: $name"
  else
    gh api -X POST "repos/$repo/labels" \
      -f name="$name" -f color="$color" -f description="$desc" >/dev/null
    echo "created: $name"
  fi
done

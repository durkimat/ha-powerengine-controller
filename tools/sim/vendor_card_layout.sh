#!/usr/bin/env bash
# Rebuilds tools/sim/viewer/plan_layout.js from the card's own plan-chart layout code (../ha-powerengine-card), so the
# simulator lays the plan out exactly as the card does. The functions are copied verbatim; nothing is changed in the card.
set -eu
cd "$(dirname "$0")/../.."
card="../ha-powerengine-card/ha-powerengine-card.js"
[ -f "$card" ] || { echo "card not found at $card"; exit 1; }
out="tools/sim/viewer/plan_layout.js"
rev="$(git -C ../ha-powerengine-card log -1 --format=%h 2>/dev/null || echo unknown)"
{
  echo "// VENDORED from the card ($card @ $rev) by tools/sim/vendor_card_layout.sh. Do not edit: rerun the script."
  echo "// The plan chart's layout (timelineLayout, sunLayout, levelAtHour, timelineHover) and the helpers it uses."
  awk '/^function toNumber\(/,/^}/' "$card"
  awk '/^const V2_MODES = \{/,/^function v2Mode\(/' "$card"
  awk '/^function v2Ms\(/,/^function v2Pct\(/' "$card"
  awk '/^\/\/ ---- plan: timeline layout/{f=1} /^\/\/ ---- plan: value map/{f=0} f' "$card"
  echo "if (typeof window !== 'undefined') window.PL = { toNumber, v2Mode, v2Ms, v2P, v2Pct, timelineLayout, levelAtHour, timelineHover, bandLabel, V2_MODES, HOUR_MS };"
} > "$out"
node --check "$out" && echo "wrote $out from card $rev ($(wc -l < "$out") lines)"

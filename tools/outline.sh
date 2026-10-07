#!/usr/bin/env bash
# A map of the two big files, so a session reads one range instead of scanning the whole file.
#
#   tools/outline.sh [app|card|<file>] [--defs]
#
# Prints each labelled section with its line number (the "# ---" / "// ---" / "/* ---" headers) and the line it ends on.
# --defs also lists the classes and methods (app) or classes, custom elements and top-level functions (card) inside each
# section. Then read just the range: Read with offset and limit, or `sed -n 'A,Bp'`.
set -u
cd "$(dirname "$0")/.."
target="app"; defs=0
for a in "$@"; do case "$a" in --defs) defs=1 ;; -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; *) target="$a" ;; esac; done
case "$target" in
  app) file="apps/powerengine/powerengine.py"; kind=py ;;
  card) file="../ha-powerengine-card/ha-powerengine-card.js"; kind=js
        [ -f "$file" ] || { echo "card repo not found at $file (clone it beside this repo)"; exit 1; } ;;
  *) file="$target"; [ -f "$file" ] || { echo "no such file: $file"; exit 1; }
     case "$file" in *.js) kind=js ;; *) kind=py ;; esac ;;
esac
total="$(wc -l < "$file")"
if [ "$kind" = py ]; then
  marker='^[[:space:]]*# ---+ ?[^-]'; def='^[[:space:]]*(class|def) [A-Za-z_]'
else
  marker='^(/\*|//) ?---+ ?[^-]'; def='^(class [A-Za-z_]|function [A-Za-z_]|customElements\.define|const [A-Z_]+ = )'
fi
echo "$file ($total lines)"
MARKER="$marker" DEF="$def" awk -v defs="$defs" -v total="$total" '
  BEGIN { marker = ENVIRON["MARKER"]; def = ENVIRON["DEF"] }
  function flush() { if (name != "") printf "%5d-%-5d %s\n%s", start, NR_end, name, buf }
  $0 ~ marker { NR_end = NR - 1; flush(); start = NR; buf = "";
    name = $0; sub(/^[[:space:]]*(#|\/\/|\/\*) ?-+ ?/, "", name); sub(/ ?-+ ?(\*\/)?[[:space:]]*$/, "", name); sub(/ ?\*\/[[:space:]]*$/, "", name)
    if (length(name) > 90) name = substr(name, 1, 87) "..."; next }
  defs == 1 && name != "" && $0 ~ def { l = $0; sub(/^[[:space:]]+/, "", l); sub(/[({:].*$/, "", l); buf = buf sprintf("%12s%d  %s\n", "", NR, l) }
  END { NR_end = total; flush() }
' "$file"

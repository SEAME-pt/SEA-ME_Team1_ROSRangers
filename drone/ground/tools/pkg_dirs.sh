#!/usr/bin/env bash
# pkg_dirs.sh SRC PKG...: the folder of each ground ROS package, one per line,
# in the order given. Used by the Makefile and by CI (ground-ros2), so both
# find the packages the same way (#186).
#
# A package lives in SRC/<component>/<package>/ (src/gesture/gesture_node,
# src/flight_interface/flight_interface). It is found by its folder, never
# derived from its name. Exit 1, with every problem on stderr, when a package
# has no folder, has more than one, or its package.xml has another <name>.
set -euo pipefail

if (($# < 2)); then
    echo "usage: $0 SRC PKG..." >&2
    exit 2
fi
src=${1%/}
shift

dirs=() errors=0
for pkg in "$@"; do
    shopt -s nullglob
    found=("$src"/*/"$pkg"/package.xml)
    shopt -u nullglob
    if ((${#found[@]} != 1)); then
        echo "pkg_dirs.sh: $pkg: expected one $src/<component>/$pkg/package.xml, found ${#found[@]}${found[*]:+: ${found[*]}}" >&2
        errors=1
        continue
    fi
    if ! grep -qE "<name>[[:space:]]*${pkg}[[:space:]]*</name>" "${found[0]}"; then
        echo "pkg_dirs.sh: ${found[0]}: <name> is not $pkg (the folder must be named after the package)" >&2
        errors=1
        continue
    fi
    dirs+=("${found[0]%/package.xml}")
done

((errors)) && exit 1
printf '%s\n' "${dirs[@]}"

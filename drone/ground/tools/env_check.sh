#!/usr/bin/env bash
# env_check.sh MODE: env/MODE.env is there and filled in, before a rule starts
# that mode (ADR-025 E1.3). Run from drone/ground/ (the Makefile does).
#
# The env files hold the values the safety pilot gives out (ROS domain,
# discovery range); git ignores them, and their templates in env.example/
# leave those values empty. Checked, with every problem on stderr:
#   * env/MODE.env exists;
#   * COMPOSE_PROFILES and GROUND_MODE are MODE (the file is that mode's);
#   * GROUND_DOMAIN_ID is a whole number 0-101, not 81-98 (the tests'), not
#     0 or 42 (the domains native runs use by default);
#   * GROUND_DISCOVERY_RANGE is LOCALHOST or SUBNET;
#   * no other filled-in env/*.env uses the same domain.
# The file is read with grep, never sourced: nothing in it runs.
set -euo pipefail

if (($# != 1)); then
    echo "usage: $0 MODE" >&2
    exit 2
fi
mode=$1
file=env/$mode.env
template=env.example/$mode.env.example
setup="mkdir -p env && cp $template $file, then fill in the values the safety pilot gives you"

# value FILE KEY: the last KEY=value in FILE, without quotes or spaces
value() {
    local line
    line=$(grep -E "^[[:space:]]*$2=" "$1" | tail -n 1 || true)
    line=${line#*=}
    line=${line//[\"\' ]/}
    echo "$line"
}

if [[ ! -f $file ]]; then
    echo "make: $file is missing: $setup" >&2
    exit 1
fi

errors=0
problem() {
    echo "make: $file: $*" >&2
    errors=1
}

for key in COMPOSE_PROFILES GROUND_MODE; do
    [[ $(value "$file" "$key") == "$mode" ]] ||
        problem "$key must be $mode (copied from $template)"
done

domain=$(value "$file" GROUND_DOMAIN_ID)
if [[ -z $domain ]]; then
    problem "GROUND_DOMAIN_ID is empty: ask the safety pilot"
elif ! [[ $domain =~ ^[0-9]+$ ]] || ((10#$domain > 101)); then
    problem "GROUND_DOMAIN_ID must be a whole number 0-101, got '$domain'"
elif ((10#$domain >= 81 && 10#$domain <= 98)); then
    problem "GROUND_DOMAIN_ID $domain is in the tests' range 81-98"
elif ((10#$domain == 0 || 10#$domain == 42)); then
    # 0: a terminal with no ROS_DOMAIN_ID; 42: the native launch files'
    # default. A native flight_interface would join the mode (ADR-025 E1.4)
    problem "GROUND_DOMAIN_ID $domain is the domain native runs use by default (0 or 42)"
fi

range=$(value "$file" GROUND_DISCOVERY_RANGE)
if [[ -z $range ]]; then
    problem "GROUND_DISCOVERY_RANGE is empty: ask the safety pilot"
elif [[ $range != LOCALHOST && $range != SUBNET ]]; then
    problem "GROUND_DISCOVERY_RANGE must be LOCALHOST or SUBNET, got '$range'"
fi

# Two modes on one domain would hear each other
if [[ -n $domain ]]; then
    for other in env/*.env; do
        [[ $other == "$file" || ! -f $other ]] && continue
        if [[ $(value "$other" GROUND_DOMAIN_ID) == "$domain" ]]; then
            problem "GROUND_DOMAIN_ID $domain is also $other's: each mode needs its own"
        fi
    done
fi

((errors)) && exit 1
exit 0

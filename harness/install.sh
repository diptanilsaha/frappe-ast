#!/bin/sh
# Link the bench agent guide into one or more bench roots: harness/install.sh ~/benches/bench16 ...
# Existing AGENTS.md or CLAUDE.md files are left alone.
set -eu
guide="$(cd "$(dirname "$0")" && pwd)/AGENTS.md"
for bench in "$@"; do
	[ -d "$bench/apps" ] && [ -d "$bench/sites" ] || { echo "skip $bench: not a bench"; continue; }
	for name in AGENTS.md CLAUDE.md; do
		if [ -e "$bench/$name" ] || [ -L "$bench/$name" ]; then
			echo "keep $bench/$name (exists)"
		else
			ln -s "$guide" "$bench/$name" && echo "link $bench/$name"
		fi
	done
done

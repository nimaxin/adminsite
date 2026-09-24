#!/bin/sh
# Put the demo at one commit of main, and rebuild it.
#
# On the server this is /srv/adminsite-demo/deploy.sh, and the deploy key may
# run nothing else, so the most a leaked key can do is deploy a commit that
# is already on main:
#
#   ssh deploy@adminsite.duckdns.org <40-character commit hash>
set -eu

commit="${SSH_ORIGINAL_COMMAND:-${1:-}}"
case "$commit" in
*[!0-9a-f]*)
	echo "Give a commit hash, not '$commit'." >&2
	exit 1
	;;
esac
if [ "${#commit}" -ne 40 ]; then
	echo "Give the full 40-character commit hash." >&2
	exit 1
fi

cd /srv/adminsite-demo/repo
git fetch --quiet origin main
if ! git merge-base --is-ancestor "$commit" origin/main; then
	echo "$commit is not on main." >&2
	exit 1
fi
git checkout --quiet --detach "$commit"

docker compose --file demo/compose.yaml --env-file ../.env \
	up --detach --build --remove-orphans --wait
docker image prune --force >/dev/null
echo "The demo now runs $commit."

#!/bin/sh
set -eu
umask 077
[ "$#" = 2 ] || { echo 'usage: ecs-artifact-deploy.sh <branch> <bundle-dir>' >&2; exit 64; }
[ "$1" = test ] || { echo 'only test may deploy' >&2; exit 64; }
bundle=$(CDPATH= cd -- "$2" && pwd)
cd "$bundle"
test -s SHA256SUMS
sha256sum --strict --check SHA256SUMS >/dev/null
for file in image.tar.gz image-ref.txt image-id.txt deploy/image-identity.py deploy/runtime-preflight.py deploy/docker-compose.ecs-test.yml; do
  test -s "$file"
  awk -v name="$file" '$2 == name || $2 == "*" name { found=1 } END { exit !found }' SHA256SUMS
done
image=$(cat image-ref.txt)
sha=${image#juya-miniapp-api-test:}
[ "$image" = "juya-miniapp-api-test:$sha" ] || exit 64
case "$sha" in ''|*[!a-f0-9]*) exit 64;; esac
[ "${#sha}" = 40 ] || exit 64

root=/opt/juya/juya-miniapp-api
if [ -n "${JUYA_DEPLOY_TEST_ROOT:-}" ]; then
  [ "${JUYA_DEPLOY_TEST_MODE:-}" = 1 ] || exit 64
  root=$JUYA_DEPLOY_TEST_ROOT
fi
case "$root" in /*) ;; *) exit 64;; esac
env_file=${JUYA_COMPOSE_ENV_FILE:-/etc/juya/miniapp-compose.env}
test -r "$env_file"
mkdir -p "$root/releases"
exec 9>"$root/.artifact-deploy.lock"
flock -n 9 || { echo 'another miniapp deployment is running' >&2; exit 75; }

# Reject a rebuilt SHA before docker load can replace the active release's tag.
release="$root/releases/$sha"
if [ -d "$release" ]; then
  cmp "$bundle/SHA256SUMS" "$release/SHA256SUMS" || { echo 'release already exists with different content' >&2; exit 65; }
fi

# Same Docker config/OCI digest verification used by admin-api.
expected_id=$(cat "$bundle/image-id.txt")
python3 "$bundle/deploy/image-identity.py" "$bundle/image.tar.gz" "$image" >/dev/null
docker load --input "$bundle/image.tar.gz" >/dev/null
actual_id=$(docker image inspect --format '{{.Id}}' "$image")
python3 "$bundle/deploy/image-identity.py" "$bundle/image.tar.gz" "$image" "$expected_id" "$actual_id" >/dev/null
if [ ! -d "$release" ]; then
  mkdir "$release"
  cp -R "$bundle/deploy" "$release/deploy"
  cp "$bundle/image-ref.txt" "$bundle/image-id.txt" "$bundle/SHA256SUMS" "$release/"
fi
compose() {
  JUYA_MINIAPP_API_IMAGE="$image" docker compose --env-file "$env_file" -p juya-miniapp-test \
    -f "$release/deploy/docker-compose.ecs-test.yml" "$@"
}
previous_release=
if [ -L "$root/current-release" ]; then
  previous_release=$(readlink -f "$root/current-release")
  previous_image=$(cat "$previous_release/image-ref.txt")
fi
previous_compose() {
  JUYA_MINIAPP_API_IMAGE="$previous_image" docker compose --env-file "$env_file" -p juya-miniapp-test \
    -f "$previous_release/deploy/docker-compose.ecs-test.yml" "$@"
}
compose config --quiet
# Test mode can return readiness=200 with no business routes; require real runtime.
# umask 077 makes copied files root-only; the image runs as a non-root user.
# This script contains no credentials and is mounted read-only in the container.
chmod 0444 "$release/deploy/runtime-preflight.py"
compose run --rm --no-deps --entrypoint python \
  -v "$release/deploy/runtime-preflight.py:/tmp/runtime-preflight.py:ro" \
  miniapp-api /tmp/runtime-preflight.py

changed=0
success=0
finish() {
  status=$?
  trap - EXIT HUP INT TERM
  if [ "$success" != 1 ] && [ "$changed" = 1 ]; then
    if [ -n "$previous_release" ]; then
      echo 'deployment failed; restoring previous miniapp application' >&2
      previous_compose up -d --no-deps miniapp-api miniapp-worker miniapp-beat || \
        echo 'previous application restore failed; manual recovery required' >&2
    else
      echo 'first deployment failed; stopping incomplete miniapp application' >&2
      compose stop miniapp-beat miniapp-worker miniapp-api || true
    fi
  fi
  exit "$status"
}
trap finish EXIT
trap 'exit 130' HUP INT TERM
changed=1
if [ -n "$previous_release" ]; then
  previous_compose stop miniapp-beat
  worker_container=$(previous_compose ps -q miniapp-worker)
  previous_compose stop -t 180 miniapp-worker
  if [ -n "$worker_container" ]; then
    [ "$(docker inspect --format '{{.State.ExitCode}}' "$worker_container")" != 137 ] || {
      echo 'worker exceeded graceful shutdown budget; deployment aborted' >&2; exit 75;
    }
  fi
  previous_compose stop miniapp-api
fi
# This project owns no migrations or database/Redis volumes.
compose up -d --no-deps miniapp-api
address=$(compose port miniapp-api 8000)
case "$address" in 127.0.0.1:[0-9]*) ;; *) echo 'expected loopback-only API port' >&2; exit 64;; esac
curl --fail --silent --show-error --max-time 5 --retry 30 --retry-delay 2 --retry-all-errors \
  "http://$address/health/ready" >/dev/null
compose up -d --no-deps miniapp-worker miniapp-beat
for pass in 1 2; do
  for service in miniapp-api miniapp-worker miniapp-beat; do
    container=$(compose ps -q "$service")
    test -n "$container" || { echo "$service is missing" >&2; exit 1; }
    [ "$(docker inspect --format '{{.State.Status}} {{.RestartCount}}' "$container")" = 'running 0' ] || {
      echo "$service did not start cleanly" >&2; exit 1;
    }
  done
  if [ "$pass" = 1 ]; then sleep 2; fi
done
compose ps
next="$root/.current-release.$$.next"
ln -s "$release" "$next"
mv -Tf "$next" "$root/current-release"
success=1
printf 'deployed %s\n' "$image"

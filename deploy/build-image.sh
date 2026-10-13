#!/bin/sh
# Export BuildKit cache as files so Flow's remote file cache can persist it.
set -eu
[ "$#" = 2 ] || { echo 'usage: build-image.sh <image> <context>' >&2; exit 64; }
image=$1
context=$2
mode=${JUYA_IMAGE_CACHE_MODE:-auto}
case "$mode" in auto|off) ;; *) echo 'image cache mode must be auto or off' >&2; exit 64;; esac
if [ "$mode" = off ]; then
  exec docker build --progress=plain --platform linux/amd64 \
    --build-arg "JUYA_SOURCE_COMMIT=${image##*:}" --tag "$image" "$context"
fi

cache_dir=${JUYA_BUILDKIT_CACHE_DIR:-${XDG_CACHE_HOME:-${HOME:-/tmp}/.cache}/juya-miniapp-buildkit}
case "$cache_dir" in /*) ;; *) echo 'BuildKit cache directory must be absolute' >&2; exit 64;; esac
mkdir -p "$cache_dir"
cache_dir=$(CDPATH= cd -- "$cache_dir" && pwd -P)
current="$cache_dir/current"
[ ! -L "$current" ] || { echo 'BuildKit cache must not be a symlink' >&2; exit 64; }
# SIGKILL cannot run EXIT traps. Recover a completed prior cache first, then
# discard only our six-character mktemp generations from interrupted runs.
# Flow restores a private directory per task; do not share it with live jobs.
for stale in "$cache_dir"/swap.??????; do
  [ -d "$stale" ] && [ ! -L "$stale" ] || continue
  if [ ! -e "$current" ] && [ -d "$stale/previous" ] && \
     [ ! -L "$stale/previous" ] && [ -s "$stale/previous/index.json" ]; then
    printf '[artifact] recovering interrupted cache rotation\n'
    mv "$stale/previous" "$current"
  fi
done
for stale in "$cache_dir"/export.?????? "$cache_dir"/swap.??????; do
  [ -d "$stale" ] && [ ! -L "$stale" ] || continue
  rm -rf "$stale"
done
probe=$(mktemp -d "${TMPDIR:-/tmp}/juya-build-cache-probe.XXXXXX")
next=
swap=
cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  if [ -n "$swap" ] && [ -d "$swap/previous" ] && [ ! -e "$current" ]; then
    mv "$swap/previous" "$current"
  fi
  # Only remove this invocation's mktemp directories, never the restored cache.
  rm -rf "$probe"
  if [ -n "$next" ]; then rm -rf "$next"; fi
  if [ -n "$swap" ]; then rm -rf "$swap"; fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM
printf 'FROM scratch\nCOPY marker /juya-cache-probe\n' > "$probe/Dockerfile"
printf 'juya-cache-probe\n' > "$probe/marker"
# A CLI existing does not prove Flow's Docker proxy supports cache exporters.
# This probe pulls no image, runs no container and writes only temporary files.
if ! docker buildx version >/dev/null 2>&1 || \
   ! docker buildx build --progress=plain --platform linux/amd64 \
     --cache-to "type=local,dest=$probe/cache" --output type=cacheonly "$probe" \
     > "$probe/build.log" 2>&1 || [ ! -s "$probe/cache/index.json" ]; then
  printf '[artifact] local cache exporter unavailable; using docker build and platform cache\n'
  docker build --progress=plain --platform linux/amd64 \
    --build-arg "JUYA_SOURCE_COMMIT=${image##*:}" --tag "$image" "$context"
  exit 0
fi

next=$(mktemp -d "$cache_dir/export.XXXXXX")
set -- docker buildx build --progress=plain --platform linux/amd64 --load \
  --build-arg "JUYA_SOURCE_COMMIT=${image##*:}" --tag "$image" \
  --cache-to "type=local,dest=$next,mode=max"
if [ -s "$current/index.json" ]; then
  printf '[artifact] importing previous BuildKit cache\n'
  set -- "$@" --cache-from "type=local,src=$current"
else
  printf '[artifact] no previous BuildKit cache; creating first cache\n'
fi
# Build errors must block artifact publication; never retry them as a cache miss.
"$@" "$context"
test -s "$next/index.json"
swap=$(mktemp -d "$cache_dir/swap.XXXXXX")
if [ -d "$current" ]; then mv "$current" "$swap/previous"; fi
mv "$next" "$current"
next=
printf '[artifact] BuildKit cache refreshed\n'

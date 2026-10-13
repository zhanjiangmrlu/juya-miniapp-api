#!/bin/sh
set -eu
[ "$#" = 3 ] || { echo 'usage: build-artifact.sh <branch> <commit-sha|auto> <output>' >&2; exit 64; }
[ "$1" = test ] || { echo 'only test may build test artifacts' >&2; exit 64; }
supplied_sha=$2
if [ "$supplied_sha" != auto ]; then
  case "$supplied_sha" in ''|*[!a-f0-9]*) exit 64;; esac
  [ "${#supplied_sha}" = 40 ] || exit 64
fi
output=$3
[ ! -e "$output" ] || { echo 'output must not already exist' >&2; exit 64; }
level=${JUYA_IMAGE_GZIP_LEVEL-3}
case "$level" in [1-9]) ;; *) echo 'gzip level must be 1-9' >&2; exit 64;; esac
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
sha=$(git -C "$source_dir" rev-parse --verify HEAD)
case "$sha" in ''|*[!a-f0-9]*) exit 64;; esac
[ "${#sha}" = 40 ] || exit 64
printf '[artifact] miniapp checkout SHA=%s\n' "$sha"
if [ "$supplied_sha" != auto ] && [ "$supplied_sha" != "$sha" ]; then
  printf '[artifact] CI SHA=%s differs from miniapp checkout; using checkout SHA\n' "$supplied_sha"
fi
image="juya-miniapp-api-test:$sha"
# 云效代理可能拒绝版本查询;镜像构建、导出和校验仍须成功。
command -v docker >/dev/null
command -v python3 >/dev/null
timed() {
  stage=$1
  shift
  started=$(date +%s)
  printf '[artifact] %s start\n' "$stage"
  "$@"
  printf '[artifact] %s elapsed=%ss\n' "$stage" "$(( $(date +%s) - started ))"
}
timed docker-build sh "$script_dir/build-image.sh" "$image" "$source_dir"
mkdir -p "$output"
timed docker-save docker save --output "$output/image.tar" "$image"
write_identity() {
  python3 "$script_dir/image-identity.py" "$output/image.tar" "$image" > "$output/image-id.txt"
}
timed image-identity write_identity
if command -v pigz >/dev/null; then
  timed image-compress pigz -n "-$level" "$output/image.tar"
else
  timed image-compress gzip -n "-$level" "$output/image.tar"
fi
printf '%s\n' "$image" > "$output/image-ref.txt"
cp -R "$script_dir" "$output/deploy"
(
  cd "$output"
  find deploy -type f -exec sha256sum {} \; > SHA256SUMS
  sha256sum image.tar.gz image-ref.txt image-id.txt >> SHA256SUMS
)
printf '[artifact] compressed image bytes=%s gzip-level=%s\n' "$(wc -c < "$output/image.tar.gz" | tr -d ' ')" "$level"

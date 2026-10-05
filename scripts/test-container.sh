#!/bin/sh
# Run the same isolated checks locally and in CI, then remove task-owned resources.
set -eu

cd "$(dirname "$0")/.."
PTV_TEST_SUFFIX="$(git rev-parse --short HEAD)-$$"
PTV_TEST_IMAGE="ptv-test-harness:$PTV_TEST_SUFFIX"
PTV_TEST_CONTAINER="ptv-tests-$PTV_TEST_SUFFIX"
PTV_TEST_BUILDER="ptv-tests-$PTV_TEST_SUFFIX"

cleanup_ptv_tests() {
    PTV_TEST_STATUS=$?
    docker rm -f "$PTV_TEST_CONTAINER" >/dev/null 2>&1 || true
    docker image rm "$PTV_TEST_IMAGE" >/dev/null 2>&1 || true
    docker buildx rm "$PTV_TEST_BUILDER" >/dev/null 2>&1 || true
    if docker container inspect "$PTV_TEST_CONTAINER" >/dev/null 2>&1 \
        || docker image inspect "$PTV_TEST_IMAGE" >/dev/null 2>&1 \
        || docker buildx inspect "$PTV_TEST_BUILDER" >/dev/null 2>&1; then
        printf '%s\n' "Cleanup incomplete: check $PTV_TEST_CONTAINER, $PTV_TEST_IMAGE and $PTV_TEST_BUILDER." >&2
        PTV_TEST_STATUS=1
    fi
    exit "$PTV_TEST_STATUS"
}
trap cleanup_ptv_tests EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

run_ptv_check() {
    docker run --rm --name "$PTV_TEST_CONTAINER" --network none --read-only \
        --tmpfs /tmp:rw,mode=1777,size=512m "$PTV_TEST_IMAGE" "$@"
}

docker buildx create --name "$PTV_TEST_BUILDER" --driver docker-container
docker buildx build --builder "$PTV_TEST_BUILDER" --load \
    -f Dockerfile.test -t "$PTV_TEST_IMAGE" .
run_ptv_check python -m ruff check tests
run_ptv_check python -m ruff format --check tests
run_ptv_check python -m pytest -o cache_dir=/tmp/pytest-cache

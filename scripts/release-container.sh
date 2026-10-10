#!/bin/sh
# Explicitly networked installation/live-API trial, separate from offline CI.
set -eu
if [ "$#" -ne 2 ] || [ "$1" != "--networked" ]; then
    printf '%s\n' 'Usage: sh scripts/release-container.sh --networked <candidate-ref>' \
        'Pass a JSON credential object on stdin; never put credentials in arguments.' >&2
    exit 2
fi
cd "$(dirname "$0")/.."
PTV_RELEASE_REF=$2
PTV_RELEASE_SUFFIX="$(git rev-parse --short HEAD)-$$"
PTV_RELEASE_IMAGE="ptv-release-check:$PTV_RELEASE_SUFFIX"
PTV_RELEASE_CONTAINER="ptv-release-check-$PTV_RELEASE_SUFFIX"
PTV_RELEASE_BUILDER="ptv-release-check-$PTV_RELEASE_SUFFIX"

verify_release_absent() {
    PTV_RELEASE_RESOURCE=$1
    shift
    if PTV_RELEASE_RESOURCES=$("$@"); then
        for PTV_RELEASE_EXISTING in $PTV_RELEASE_RESOURCES; do
            if [ "$PTV_RELEASE_EXISTING" = "$PTV_RELEASE_RESOURCE" ]; then
                printf '%s\n' "Cleanup incomplete: $PTV_RELEASE_RESOURCE remains." >&2
                return 1
            fi
        done
    else
        printf '%s\n' "Cleanup unverified: could not check $PTV_RELEASE_RESOURCE." >&2
        return 1
    fi
}
cleanup_release() {
    PTV_RELEASE_STATUS=$?
    docker rm -f "$PTV_RELEASE_CONTAINER" >/dev/null 2>&1 || true
    docker image rm "$PTV_RELEASE_IMAGE" >/dev/null 2>&1 || true
    docker buildx rm "$PTV_RELEASE_BUILDER" >/dev/null 2>&1 || true
    verify_release_absent "$PTV_RELEASE_CONTAINER" \
        docker container ls --all --format '{{.Names}}' || PTV_RELEASE_STATUS=1
    verify_release_absent "$PTV_RELEASE_IMAGE" \
        docker image ls --format '{{.Repository}}:{{.Tag}}' || PTV_RELEASE_STATUS=1
    verify_release_absent "$PTV_RELEASE_BUILDER" \
        docker buildx ls --format '{{.Name}}' || PTV_RELEASE_STATUS=1
    if [ "$PTV_RELEASE_STATUS" -ne 0 ]; then
        printf '%s\n' 'Task-only recovery commands:' \
            "docker rm -f $PTV_RELEASE_CONTAINER" \
            "docker image rm $PTV_RELEASE_IMAGE" \
            "docker buildx rm $PTV_RELEASE_BUILDER" >&2
    fi
    exit "$PTV_RELEASE_STATUS"
}
trap cleanup_release EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

docker buildx create --name "$PTV_RELEASE_BUILDER" --driver docker-container
docker buildx build --builder "$PTV_RELEASE_BUILDER" --load \
    -f Dockerfile.release -t "$PTV_RELEASE_IMAGE" .
docker run --rm --interactive --name "$PTV_RELEASE_CONTAINER" \
    --network bridge --read-only --tmpfs /tmp:rw,mode=1777,size=512m \
    "$PTV_RELEASE_IMAGE" "$PTV_RELEASE_REF"

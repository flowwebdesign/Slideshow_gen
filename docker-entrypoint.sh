#!/bin/sh
set -eu

data_dir="${SLIDESHOW_DATA_DIR:-/data}"

case "$data_dir" in
    /*) ;;
    *)
        echo "SLIDESHOW_DATA_DIR must be an absolute path" >&2
        exit 1
        ;;
esac

if [ "$data_dir" = "/" ]; then
    echo "SLIDESHOW_DATA_DIR cannot be the filesystem root" >&2
    exit 1
fi

mkdir -p "$data_dir/jobs"
chown slideshow:slideshow "$data_dir" "$data_dir/jobs"

for database_file in "$data_dir"/jobs.sqlite3*; do
    [ ! -e "$database_file" ] || chown slideshow:slideshow "$database_file"
done

exec gosu slideshow "$@"

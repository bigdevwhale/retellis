#!/bin/sh
# Retellis web entrypoint.
#
# Why this exists: Next.js standalone streams EVERY response chunked
# (Transfer-Encoding: chunked, no Content-Length). Over a long/lossy path to
# the browser the final zero-chunk can be lost → ERR_INCOMPLETE_CHUNKED_ENCODING
# → ChunkLoadError → blank page. The fix is to serve /_next/static with a known
# Content-Length (precompressed gzip served by Caddy's file_server), so the chunked
# stream is never on the wire for static.
#
# This entrypoint syncs the build's precompressed static (baked into the image,
# incl. *.gz siblings produced in Dockerfile.web) into the shared `next_static`
# volume that Caddy mounts read-only. Copying on every start (not just first
# mount) keeps the volume in sync with the image across rebuilds — so the chunk
# hashes Caddy serves always match the HTML Next.js serves (no hash desync 404s).
#
# Then execs the Next.js standalone server as PID 1.
set -e

SRC="/app/apps/web/.next/static"
DST="/srv/static"

if [ -d "$SRC" ]; then
  mkdir -p "$DST"
  rm -rf "$DST"/*
  cp -a "$SRC/." "$DST"/
fi

exec "$@"
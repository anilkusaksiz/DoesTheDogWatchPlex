#!/bin/sh
set -e

# Generate config.py from environment variables
cat > /app/config.py <<EOF
PLEX_URL = "${PLEX_URL}"
PLEX_TOKEN = "${PLEX_TOKEN}"
DTDD_API_KEY = "${DTDD_API_KEY}"
SEPARATOR = "\n\n———— Content Warnings (via DoesTheDogDie.com) ————"
MIN_YES_VOTES = ${MIN_YES_VOTES:-5}
MIN_YES_RATIO = ${MIN_YES_RATIO:-0.7}
SHOW_SAFE_TOPICS = ${SHOW_SAFE_TOPICS:-False}
SHOW_COMMENTS = ${SHOW_COMMENTS:-False}
WARNINGS_POSITION = "${WARNINGS_POSITION:-bottom}"
COMMENT_MAX_LENGTH = ${COMMENT_MAX_LENGTH:-120}
CACHE_TTL = 604800
API_DELAY = 2.0
INCLUDE_TV_SHOWS = ${INCLUDE_TV_SHOWS:-False}
DRY_RUN = ${DRY_RUN:-False}
EOF

# Handle PLEX_LIBRARIES (comma-separated string to Python list)
if [ -n "$PLEX_LIBRARIES" ]; then
    # Convert "Movies,TV Shows" to ["Movies", "TV Shows"]
    PYLIST=$(echo "$PLEX_LIBRARIES" | python3 -c "
import sys
libs = [l.strip() for l in sys.stdin.read().strip().split(',') if l.strip()]
print(repr(libs))
")
    echo "PLEX_LIBRARIES = $PYLIST" >> /app/config.py
else
    echo "PLEX_LIBRARIES = None" >> /app/config.py
fi

# Handle INCLUDE_TOPICS (comma-separated string to Python list)
if [ -n "$INCLUDE_TOPICS" ]; then
    PYLIST=$(echo "$INCLUDE_TOPICS" | python3 -c "
import sys
topics = [t.strip() for t in sys.stdin.read().strip().split(',') if t.strip()]
print(repr(topics))
")
    echo "INCLUDE_TOPICS = $PYLIST" >> /app/config.py
else
    echo "INCLUDE_TOPICS = None" >> /app/config.py
fi

# Handle EXCLUDE_TOPICS (comma-separated string to Python list)
if [ -n "$EXCLUDE_TOPICS" ]; then
    PYLIST=$(echo "$EXCLUDE_TOPICS" | python3 -c "
import sys
topics = [t.strip() for t in sys.stdin.read().strip().split(',') if t.strip()]
print(repr(topics))
")
    echo "EXCLUDE_TOPICS = $PYLIST" >> /app/config.py
else
    echo "EXCLUDE_TOPICS = None" >> /app/config.py
fi

# Handle LANGUAGE
if [ -n "$LANGUAGE" ]; then
    echo "LANGUAGE = \"${LANGUAGE}\"" >> /app/config.py
else
    echo "LANGUAGE = None" >> /app/config.py
fi

# Validate required vars
if [ -z "$PLEX_URL" ] || [ -z "$PLEX_TOKEN" ] || [ -z "$DTDD_API_KEY" ]; then
    echo "ERROR: PLEX_URL, PLEX_TOKEN, and DTDD_API_KEY are required."
    echo ""
    echo "Example:"
    echo "  docker run -e PLEX_URL=http://plex:32400 -e PLEX_TOKEN=xxx -e DTDD_API_KEY=xxx ghcr.io/you/doesthedogwatchplex"
    exit 1
fi

# If SCHEDULE is set, run on a loop. Otherwise run once and exit.
if [ -n "$SCHEDULE" ]; then
    echo "Running on schedule: every ${SCHEDULE} seconds"
    while true; do
        echo ""
        echo "=== $(date) ==="
        python3 /app/plex_warnings.py "$@"
        echo "Sleeping ${SCHEDULE}s..."
        sleep "$SCHEDULE"
    done
else
    python3 /app/plex_warnings.py "$@"
fi

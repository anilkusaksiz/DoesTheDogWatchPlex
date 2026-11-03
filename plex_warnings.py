#!/usr/bin/env python3
"""
DoesTheDogWatchPlex — Content warnings from DoesTheDogDie.com in your Plex library.

Usage:
    python plex_warnings.py              # Process all configured libraries
    python plex_warnings.py --dry-run    # Preview changes without writing
    python plex_warnings.py --clear      # Remove all content warnings from Plex
    python plex_warnings.py --clear-cache  # Clear the local DTDD API cache
    python plex_warnings.py --movie "Midsommar"  # Process a single movie by title
    python plex_warnings.py --show "Severance"   # Process a single TV show (INCLUDE_TV_SHOWS = True)
    python plex_warnings.py --list-topics  # Show all available topic names for filtering
"""
from __future__ import annotations

import argparse
import sys
import time

from plexapi.server import PlexServer

from dtdd import DTDDClient

try:
    import config
except ImportError:
    print("ERROR: config.py not found.")
    print("Copy config.py.example to config.py and fill in your details.")
    sys.exit(1)


DEFAULT_SEPARATOR = "\n\n———— Content Warnings (via DoesTheDogDie.com) ————"


def get_separator() -> str:
    return getattr(config, "SEPARATOR", DEFAULT_SEPARATOR)


def strip_warnings(summary: str) -> str:
    """Remove existing DTDD content warnings from a summary, at the top or the bottom."""
    # The default separator too, so changing SEPARATOR replaces warnings written with it
    for sep in (get_separator(), DEFAULT_SEPARATOR):
        # Top: the separator's text, warning lines, a blank line, then the original summary.
        # Warning lines never contain a blank line, so the first one ends the block.
        if summary.startswith(sep.strip("\n") + "\n"):
            parts = summary.split("\n\n", 1)
            return parts[1].lstrip("\n") if len(parts) > 1 else ""
        if sep in summary:
            return summary.split(sep)[0].rstrip()
    # Also handle the old-style separator from the original project
    if "\ndoesthedogdie:" in summary.lower():
        for i, line in enumerate(summary.split("\n")):
            if line.strip().lower().startswith("doesthedogdie:"):
                return "\n".join(summary.split("\n")[:i]).rstrip()
    return summary


def shorten_comment(comment: str, max_len: int) -> str:
    """Collapse whitespace and cut a comment at a word boundary."""
    text = " ".join(comment.split())
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


def format_warnings(media_data: dict) -> str | None:
    """Extract and format trigger warnings from DTDD media response.

    Returns a formatted string of warnings, or None if no relevant warnings found.
    """
    stats = media_data.get("topicItemStats", [])
    if not stats:
        return None

    min_yes = getattr(config, "MIN_YES_VOTES", 3)
    min_ratio = getattr(config, "MIN_YES_RATIO", 0.6)

    show_nos = getattr(config, "SHOW_SAFE_TOPICS", False)
    include_topics = getattr(config, "INCLUDE_TOPICS", None)
    exclude_topics = getattr(config, "EXCLUDE_TOPICS", None)

    warnings_yes = []
    warnings_no = []

    for stat in stats:
        yes_count = stat.get("yesSum", 0)
        no_count = stat.get("noSum", 0)
        total = yes_count + no_count
        topic = stat.get("topic", {})
        topic_name = topic.get("name", "")
        topic_not_name = topic.get("notName", "")

        if total == 0 or not topic_name:
            continue

        # Apply topic filtering
        if include_topics is not None:
            if topic_name.lower() not in [t.lower() for t in include_topics]:
                continue
        elif exclude_topics is not None:
            if topic_name.lower() in [t.lower() for t in exclude_topics]:
                continue

        ratio = yes_count / total

        if ratio >= min_ratio and yes_count >= min_yes:
            warnings_yes.append((topic_name, yes_count, no_count, stat.get("comment")))
        elif show_nos and (1 - ratio) >= min_ratio and no_count >= min_yes:
            warnings_no.append((topic_not_name, yes_count, no_count))

    if not warnings_yes and not warnings_no:
        return None

    # Translate topic names if LANGUAGE is configured
    target_lang = getattr(config, "LANGUAGE", None)
    if target_lang:
        from translate import translate_topics
        all_names = [w[0] for w in warnings_yes] + [w[0] for w in warnings_no]
        translations = translate_topics(all_names, target_lang)
    else:
        translations = None

    lines = []
    if warnings_yes and getattr(config, "SHOW_COMMENTS", False):
        # One line per warning with DTDD's top-voted comment, which tells a
        # passing mention apart from a scene the whole film builds up to
        max_len = getattr(config, "COMMENT_MAX_LENGTH", 120)
        for name, _, _, comment in warnings_yes:
            line = "⚠️  " + (translations[name] if translations else name)
            if comment:
                line += f" — “{shorten_comment(comment, max_len)}”"
            lines.append(line)
    elif warnings_yes:
        names = [translations[w[0]] if translations else w[0] for w in warnings_yes]
        lines.append("⚠️  " + " · ".join(names))
    if warnings_no:
        names = [translations[w[0]] if translations else w[0] for w in warnings_no]
        lines.append("✅  " + " · ".join(names))

    return "\n".join(lines)


# Plex library type -> DTDD itemType name
DTDD_ITEM_TYPES = {"movie": "Movie", "show": "TV Show"}


def match_item(dtdd: DTDDClient, item) -> dict | None:
    """Try to match a Plex movie or TV show to a DTDD entry.

    Strategy:
    1. Search by IMDB ID if available (most reliable)
    2. Fall back to title search, preferring same type and year
    3. Take the first result as last resort

    Returns the DTDD media data dict, or None if no match.
    """
    title = item.title
    year = item.year
    item_type = DTDD_ITEM_TYPES.get(item.type, "Movie")

    # Try IMDB ID first (available via guids on modern Plex)
    imdb_id = None
    try:
        for guid in item.guids:
            if guid.id.startswith("imdb://"):
                imdb_id = guid.id.replace("imdb://", "")
                break
    except Exception:
        pass

    if imdb_id:
        results = dtdd.search_by_imdb(imdb_id)
        if results:
            return dtdd.get_media(results[0]["id"])

    # Fall back to title search
    results = dtdd.search(title)
    if not results:
        return None

    def same_type(result) -> bool:
        return (result.get("itemType") or {}).get("name", "") == item_type

    def same_year(result) -> bool:
        return bool(year) and str(year) == str(result.get("releaseYear", ""))

    # A movie and a show often share a title and year, so check the type too
    for match in (lambda r: same_type(r) and same_year(r), same_year, same_type):
        for result in results:
            if match(result):
                return dtdd.get_media(result["id"])

    # Last resort: first result
    return dtdd.get_media(results[0]["id"])


def process_item(dtdd: DTDDClient, item, dry_run: bool = False) -> bool:
    """Process a single movie or TV show. Returns True if the summary was updated."""
    title = f"{item.title} ({item.year})" if item.year else item.title

    # Skip if already has warnings and we're not in a re-run
    original_summary = item.summary or ""
    clean_summary = strip_warnings(original_summary)

    # Try to match and get warnings
    try:
        media_data = match_item(dtdd, item)
    except Exception as e:
        print(f"  ✗ {title} — API error: {e}")
        return False

    if not media_data:
        print(f"  – {title} — not found on DTDD")
        return False

    warning_text = format_warnings(media_data)
    if not warning_text:
        if clean_summary != original_summary:
            # Warnings from an earlier run no longer qualify (votes or settings changed)
            if dry_run:
                print(f"  ✓ {title} — would remove old warnings")
            else:
                item.editSummary(clean_summary, locked=False)
                print(f"  ✓ {title} — old warnings removed")
            return True
        print(f"  – {title} — no significant warnings")
        return False

    if getattr(config, "WARNINGS_POSITION", "bottom") == "top":
        # Plex apps cut long summaries at "Show more", which can hide warnings at the bottom
        block = get_separator().strip("\n") + "\n" + warning_text
        new_summary = block + ("\n\n" + clean_summary if clean_summary else "")
    else:
        new_summary = clean_summary + get_separator() + "\n" + warning_text
    if new_summary == original_summary:
        print(f"  = {title} — warnings unchanged")
        return False

    if dry_run:
        print(f"  ✓ {title} — would add warnings:")
        for line in warning_text.split("\n"):
            print(f"      {line}")
        return True

    try:
        item.editSummary(new_summary)
        print(f"  ✓ {title} — warnings added")
        return True
    except Exception as e:
        print(f"  ✗ {title} — failed to update: {e}")
        return False


def clear_warnings(plex: PlexServer, library_names: list[str] | None):
    """Remove all DTDD content warnings from movie and TV show summaries."""
    libraries = get_libraries(plex, library_names)
    total_cleared = 0

    for lib in libraries:
        print(f"\nClearing warnings from: {lib.title}")
        for item in lib.all():
            original = item.summary or ""
            cleaned = strip_warnings(original)
            if cleaned != original:
                # Writing warnings locks the summary; unlock so Plex can refresh it again
                item.editSummary(cleaned, locked=False)
                print(f"  ✓ {item.title} — warnings removed")
                total_cleared += 1

    print(f"\nDone. Cleared warnings from {total_cleared} item(s).")


def get_library_types() -> list[str]:
    """Plex library types to process: movies, plus TV shows if enabled."""
    return ["movie", "show"] if getattr(config, "INCLUDE_TV_SHOWS", False) else ["movie"]


def get_libraries(plex: PlexServer, library_names: list[str] | None):
    """Get movie (and optionally TV show) libraries to process."""
    types = get_library_types()
    if library_names:
        libraries = []
        for name in library_names:
            try:
                lib = plex.library.section(name)
                if lib.type in types:
                    libraries.append(lib)
                else:
                    print(f"Warning: '{name}' is a {lib.type} library, skipping "
                          f"(set INCLUDE_TV_SHOWS = True for show libraries).")
            except Exception:
                print(f"Warning: Library '{name}' not found, skipping.")
        return libraries
    else:
        return [s for s in plex.library.sections() if s.type in types]


def main():
    parser = argparse.ArgumentParser(
        description="Add DoesTheDogDie.com content warnings to your Plex movie summaries."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without modifying Plex")
    parser.add_argument("--clear", action="store_true",
                        help="Remove all content warnings from Plex summaries")
    parser.add_argument("--clear-cache", action="store_true",
                        help="Clear the local DTDD API response cache")
    parser.add_argument("--movie", type=str,
                        help="Process a single movie by title (exact match)")
    parser.add_argument("--show", type=str,
                        help="Process a single TV show by title (needs INCLUDE_TV_SHOWS = True)")
    parser.add_argument("--list-topics", action="store_true",
                        help="Show all available DTDD topic names (for use in INCLUDE_TOPICS/EXCLUDE_TOPICS)")
    args = parser.parse_args()

    dry_run = args.dry_run or getattr(config, "DRY_RUN", False)

    # Handle cache clear
    if args.clear_cache:
        client = DTDDClient(config.DTDD_API_KEY)
        client.clear_cache()
        if not args.clear and not args.movie and not args.show and not args.list_topics:
            return

    # Handle list-topics: fetch a well-known movie to show all available topics
    if args.list_topics:
        dtdd = DTDDClient(
            api_key=config.DTDD_API_KEY,
            cache_ttl=getattr(config, "CACHE_TTL", 604800),
            api_delay=getattr(config, "API_DELAY", 1.0),
        )
        print("Fetching topic list from DTDD...\n")
        # Search a popular movie likely to have all topics rated
        results = dtdd.search("Avengers Endgame")
        if results:
            media = dtdd.get_media(results[0]["id"])
            stats = media.get("topicItemStats", [])
            topics = sorted(set(
                stat.get("topic", {}).get("name", "")
                for stat in stats
                if stat.get("topic", {}).get("name")
            ))
            print("Available topic names (copy these into INCLUDE_TOPICS or EXCLUDE_TOPICS):\n")
            for topic in topics:
                print(f'    "{topic}",')
            print(f"\n{len(topics)} topics found.")
        else:
            print("Could not fetch topics. Check your DTDD_API_KEY.")
        return

    # Connect to Plex
    print(f"Connecting to Plex at {config.PLEX_URL}...")
    try:
        plex = PlexServer(config.PLEX_URL, config.PLEX_TOKEN)
        print(f"Connected to: {plex.friendlyName}")
    except Exception as e:
        print(f"ERROR: Could not connect to Plex: {e}")
        sys.exit(1)

    library_names = getattr(config, "PLEX_LIBRARIES", None)

    # Handle clear mode
    if args.clear:
        clear_warnings(plex, library_names)
        return

    # Initialize DTDD client
    dtdd = DTDDClient(
        api_key=config.DTDD_API_KEY,
        cache_ttl=getattr(config, "CACHE_TTL", 604800),
        api_delay=getattr(config, "API_DELAY", 1.0),
    )

    if dry_run:
        print("DRY RUN — no changes will be made to Plex\n")

    # Process a single movie/show or all libraries
    single = args.movie or args.show
    if single:
        wanted_type = "movie" if args.movie else "show"
        libraries = [lib for lib in get_libraries(plex, library_names) if lib.type == wanted_type]
        found = False
        for lib in libraries:
            results = lib.search(title=single)
            for item in results:
                found = True
                process_item(dtdd, item, dry_run=dry_run)
        if not found:
            print(f"{'Movie' if args.movie else 'TV show'} '{single}' not found in Plex.")
        return

    # Process all items in configured libraries
    libraries = get_libraries(plex, library_names)
    if not libraries:
        print("No movie or TV show libraries found to process.")
        sys.exit(1)

    total_processed = 0
    total_updated = 0
    start_time = time.time()
    # Stop before the monthly API quota runs out; cached items cost nothing
    quota_floor = getattr(config, "MIN_MONTHLY_QUOTA", 100)
    quota_stop = False

    for lib in libraries:
        items = lib.all()
        noun = "shows" if lib.type == "show" else "movies"
        print(f"\nProcessing: {lib.title} ({len(items)} {noun})")
        print("-" * 50)

        for item in items:
            if dtdd.remaining_month is not None and dtdd.remaining_month < quota_floor:
                print(f"\nStopping: only {dtdd.remaining_month} DTDD requests left this month "
                      f"(MIN_MONTHLY_QUOTA = {quota_floor}).")
                quota_stop = True
                break
            total_processed += 1
            if process_item(dtdd, item, dry_run=dry_run):
                total_updated += 1
        if quota_stop:
            break

    elapsed = time.time() - start_time
    print(f"\n{'=' * 50}")
    print(f"Done in {elapsed:.1f}s")
    print(f"Processed: {total_processed} items")
    print(f"Updated:   {total_updated} items")
    left = dtdd.remaining_month if dtdd.remaining_month is not None else "unknown"
    print(f"DTDD API:  {dtdd.requests_made} requests, {left} left this month")
    if dry_run:
        print("(DRY RUN — no actual changes made)")


if __name__ == "__main__":
    main()

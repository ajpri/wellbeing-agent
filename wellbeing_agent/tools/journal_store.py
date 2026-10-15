import logging
import os
import re
from datetime import datetime
from pathlib import Path

from google.genai.errors import APIError

logger = logging.getLogger(__name__)
_index_cache: dict | None = None


def _get_fingerprint(journal_dir: Path) -> str:
    """Return a fingerprint based on file names and modification times."""
    md_files = sorted(journal_dir.glob("*.md"))
    parts = [f"{f.name}:{f.stat().st_mtime_ns}" for f in md_files]
    return "|".join(parts)


def _build_index() -> dict | None:
    """Build and cache the chunked journal corpus. Returns None if no content.

    Lightweight keyword index (no ML models) so the app runs in low-memory
    environments. Chunks are the same '---'-delimited sections used before.
    """
    journal_dir = _get_journal_dir()
    fingerprint = _get_fingerprint(journal_dir)

    global _index_cache
    if _index_cache and _index_cache["fingerprint"] == fingerprint:
        return _index_cache

    md_files = sorted(journal_dir.glob("*.md"))
    if not md_files:
        return None

    chunks: list[str] = []
    sources: list[str] = []

    for f in md_files:
        text = f.read_text().strip()
        if not text:
            continue
        sections = [s.strip() for s in text.split("---") if s.strip()]
        for section in sections:
            # Skip chunks that are only image references
            non_image_lines = [l for l in section.splitlines() if l.strip() and not l.strip().startswith("![")]
            if not non_image_lines:
                continue
            chunks.append(section)
            sources.append(f.stem)

    if not chunks:
        return None

    _index_cache = {
        "fingerprint": fingerprint,
        "chunks": chunks,
        "sources": sources,
    }
    return _index_cache


def invalidate_index():
    """Clear the cached index (called after saving a new journal)."""
    global _index_cache
    _index_cache = None


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _score_chunk(chunk: str, query_tokens: list[str], query: str) -> float:
    """Keyword relevance score for a chunk against the query."""
    chunk_lower = chunk.lower()
    chunk_tokens = _tokenize(chunk)
    if not chunk_tokens:
        return 0.0
    token_counts: dict[str, int] = {}
    for t in chunk_tokens:
        token_counts[t] = token_counts.get(t, 0) + 1

    score = 0.0
    matched_terms = 0
    for qt in query_tokens:
        tf = token_counts.get(qt, 0)
        if tf:
            matched_terms += 1
            # Diminishing returns on repeated terms; normalize by chunk length.
            score += (1.0 + 0.5 * (tf - 1)) / (1.0 + len(chunk_tokens) / 100.0)

    # Bonus for matching more distinct query terms, and for exact phrase hits.
    if query_tokens:
        score *= 1.0 + matched_terms / len(query_tokens)
    if len(query.strip()) >= 3 and query.strip().lower() in chunk_lower:
        score += 2.0
    return score


def _get_journal_dir() -> Path:
    journal_dir = Path(os.environ.get("JOURNAL_DIR", "./journals"))
    journal_dir.mkdir(parents=True, exist_ok=True)
    return journal_dir


def _cartoon_failure_message(error: Exception) -> str:
    """Give useful guidance without exposing API response bodies or credentials."""
    if isinstance(error, APIError):
        if error.code == 403:
            return "Google denied access to image generation. Check your API project's permissions and billing in Google AI Studio."
        if error.code == 401:
            return "Google could not authenticate the image request. Check your API key."
        if error.code == 429:
            return "Google reported an image generation quota or rate limit. Check your project's quota and billing before trying again later."
        if error.code == 404:
            return "The configured image model was not found or is unavailable to your project."
        if error.code and error.code >= 500:
            return "Google's image service is temporarily unavailable."
    return "Image generation failed. You can continue journaling without a cartoon."


def save_journal(entry: str, title: str = "") -> str:
    """Save a wellbeing journal entry, then attempt an optional cartoon.

    Args:
        entry: The formatted markdown journal entry to save.
        title: A short summary of the day's highlights for the cartoon image.

    Returns:
        Confirmation with the file path and any cartoon failure explanation.
    """
    now = datetime.now()
    journal_dir = _get_journal_dir()
    date_str = now.strftime("%Y-%m-%d")

    # If today's file already exists, append a numeric suffix
    file_path = journal_dir / f"{date_str}.md"
    counter = 2
    while file_path.exists():
        file_path = journal_dir / f"{date_str}-{counter}.md"
        counter += 1

    # Persist the journal before making any optional external API request.
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(entry)
    invalidate_index()
    confirmation = f"Journal entry saved to {file_path}"
    if not title:
        return confirmation

    try:
        from .image_gen import generate_day_cartoon

        cartoon_result = generate_day_cartoon(title)
        img_line = next((line.strip() for line in cartoon_result.splitlines()
                         if line.strip().startswith("![")), None)
    except Exception as error:
        # The optional image must not undo a successful save. Avoid logging raw
        # exceptions because provider responses can include sensitive data.
        logger.warning("Cartoon generation failed (%s); journal text was saved.", type(error).__name__)
        return f"{confirmation}. Cartoon not generated: {_cartoon_failure_message(error)}"

    if not img_line:
        return f"{confirmation}. Cartoon not generated: Google returned no usable image."

    try:
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(f"\n\n---\n{img_line}\n")
    except OSError:
        logger.warning("Could not attach cartoon; journal text was saved.")
        return f"{confirmation}. The cartoon could not be attached to the entry. Check local file permissions and available disk space."
    invalidate_index()
    return confirmation


def read_journal(date: str = "") -> str:
    """Read a journal entry by date, or list recent entries.

    Args:
        date: Date in YYYY-MM-DD format. Leave empty to list the last 7 entries.

    Returns:
        The journal entry content or a list of recent entries.
    """
    journal_dir = _get_journal_dir()

    if date:
        file_path = journal_dir / f"{date}.md"
        if file_path.exists():
            return file_path.read_text()
        return f"No entry found for {date}."

    entries = sorted(journal_dir.glob("*.md"), reverse=True)[:7]
    if not entries:
        return "No journal entries yet."

    return "Recent entries:\n" + "\n".join(
        f"- {e.stem}" for e in entries
    )


def search_journals(query: str, top_k: int = 3) -> str:
    """Search through all journal entries by keyword relevance.

    Ranks journal passages by how well they match the query's keywords.

    Args:
        query: The search query (e.g. "days I felt anxious", "morning runs").
        top_k: Number of top results to return (default 3).

    Returns:
        The most relevant journal passages with their source dates.
    """
    cache = _build_index()
    if cache is None:
        return "No journal content to search."

    chunks = cache["chunks"]
    sources = cache["sources"]

    query_tokens = _tokenize(query)
    if not query_tokens:
        return "No journal content to search."

    scored = [
        (_score_chunk(chunk, query_tokens, query), idx)
        for idx, chunk in enumerate(chunks)
    ]
    scored = [s for s in scored if s[0] > 0]
    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        return "No matching journal entries found."

    top = scored[: min(top_k, len(scored))]
    results: list[str] = []
    for rank, (score, idx) in enumerate(top, 1):
        results.append(
            f"### Result {rank} (date: {sources[idx]}, score: {score:.3f})\n{chunks[idx]}"
        )

    return "\n\n---\n\n".join(results)

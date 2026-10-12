import re
from collections import Counter
from pathlib import Path

KNOWLEDGE_ROOT = Path(__file__).parents[2] / "company_data"
MAX_RESULTS = 5
MAX_CHUNK_LENGTH = 1200

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "apply",
    "are",
    "for",
    "find",
    "how",
    "i",
    "in",
    "is",
    "of",
    "on",
    "the",
    "to",
    "what",
    "with",
}


def _tokens(text: str) -> list[str]:
    tokens = []
    for token in _TOKEN_PATTERN.findall(text.lower()):
        if token in _STOP_WORDS:
            continue
        if token.endswith("ing") and len(token) > 5:
            token = token[:-3]
        elif token.endswith("s") and len(token) > 4:
            token = token[:-1]
        tokens.append(token)
    return tokens


def _chunks(text: str) -> list[str]:
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip()
        if current and len(candidate) > MAX_CHUNK_LENGTH:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate

    if current:
        chunks.append(current)
    return chunks


def _load_chunks() -> list[dict[str, str]]:
    chunks = []
    for path in sorted(KNOWLEDGE_ROOT.rglob("*.md")):
        relative_path = path.relative_to(KNOWLEDGE_ROOT).as_posix()
        for content in _chunks(path.read_text(encoding="utf-8")):
            chunks.append({"source": relative_path, "content": content})
    return chunks


def search_documents(query: str, limit: int = MAX_RESULTS) -> list[dict[str, str | int]]:
    """Return the highest-scoring local Markdown chunks for a query."""
    query_tokens = Counter(_tokens(query))
    if not query_tokens:
        return []

    best_by_source: dict[str, dict[str, str | int]] = {}
    for chunk in _load_chunks():
        document_tokens = Counter(_tokens(chunk["content"]))
        content_score = sum(
            min(query_count, document_tokens[token])
            for token, query_count in query_tokens.items()
        )
        source_tokens = set(_tokens(chunk["source"]))
        source_score = 2 * sum(token in source_tokens for token in query_tokens)
        score = content_score + source_score
        if score:
            result = {**chunk, "score": score}
            source = str(chunk["source"])
            previous = best_by_source.get(source)
            if previous is None or int(result["score"]) > int(previous["score"]):
                best_by_source[source] = result

    scored = list(best_by_source.values())
    scored.sort(key=lambda result: (-int(result["score"]), str(result["source"])))
    return scored[: max(0, limit)]


def read_document(source: str) -> dict[str, str | bool]:
    """Return one whole Markdown document from the knowledge base.

    ``source`` is a path relative to the knowledge root, as returned by
    ``search_documents``. Paths outside the root and non-Markdown files are
    rejected.
    """
    root = KNOWLEDGE_ROOT.resolve()
    path = (root / source).resolve()
    if path.suffix.lower() != ".md" or root not in path.parents or not path.is_file():
        return {
            "success": False,
            "error_type": "not_found",
            "retryable": False,
            "message": f"No knowledge document found at '{source}'.",
        }
    return {
        "success": True,
        "source": path.relative_to(root).as_posix(),
        "content": path.read_text(encoding="utf-8"),
    }

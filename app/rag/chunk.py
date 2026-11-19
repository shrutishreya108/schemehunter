"""Split one page into chunks of about 900 characters on line boundaries, with about 100 characters of overlap.

Each chunk keeps the page number (the caller stores it). A line longer than the limit is cut hard.
"""

CHUNK_CHARS = 900
OVERLAP_CHARS = 100


def _split_long(line: str, size: int) -> list[str]:
    return [line[i : i + size] for i in range(0, len(line), size)]


def chunk_page(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            lines.extend(_split_long(line, size) if len(line) > size else [line])
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for line in lines:
        if current and length + 1 + len(line) > size:
            chunks.append("\n".join(current))
            # carry the last lines (up to `overlap` characters) into the next chunk
            tail: list[str] = []
            tail_len = 0
            for prev in reversed(current):
                if tail_len + len(prev) + 1 > overlap:
                    break
                tail.insert(0, prev)
                tail_len += len(prev) + 1
            current, length = tail, sum(len(x) + 1 for x in tail)
        current.append(line)
        length += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks

"""Structure-aware HTML cleanup and token-bounded chunk construction."""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from typing import Protocol

from .contracts import ChunkInput, MaterialInput, SourceBlock


class Tokenizer(Protocol):
    def encode(self, text: str, add_special_tokens: bool = ...) -> list[int]: ...
    def decode(self, token_ids: list[int], **kwargs: object) -> str: ...


@dataclass(frozen=True)
class _Segment:
    text: str
    section_path: str | None
    source_block: str


class _BlockParser(HTMLParser):
    """Tolerant HTML-to-semantic-block parser; drops active/hidden content."""

    _headings = {f"h{i}": i for i in range(1, 7)}
    _boundaries = {"p", "li", "pre", "blockquote", "td", "th", "dt", "dd"}
    _ignored = {"script", "style", "noscript", "svg", "iframe", "template"}

    def __init__(self, source_block: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source_block = source_block
        self.segments: list[_Segment] = []
        self._buffer: list[str] = []
        self._heading_text: list[str] | None = None
        self._heading_level: int | None = None
        self._heading_stack: list[str] = []
        self._ignored_stack: list[str] = []
        self._list_depth = 0

    @property
    def section_path(self) -> str | None:
        return " › ".join(self._heading_stack) if self._heading_stack else None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = dict(attrs)
        if self._ignored_stack:
            if tag in self._ignored:
                self._ignored_stack.append(tag)
            return
        style = attrs_map.get("style", "").replace(" ", "").lower()
        hidden = (
            "hidden" in attrs_map
            or attrs_map.get("aria-hidden", "").lower() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        )
        if tag in self._ignored or hidden:
            self._ignored_stack.append(tag)
            return
        if tag in self._headings:
            self._flush()
            self._heading_text = []
            self._heading_level = self._headings[tag]
        elif tag in self._boundaries:
            self._flush()
            if tag == "li":
                self._list_depth += 1
        elif tag == "br":
            self._buffer.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._ignored_stack:
            if tag == self._ignored_stack[-1]:
                self._ignored_stack.pop()
            return
        if tag in self._headings and self._heading_text is not None:
            heading = _normalize_text("".join(self._heading_text))
            level = self._heading_level or self._headings[tag]
            if heading:
                self._heading_stack = self._heading_stack[: level - 1]
                self._heading_stack.append(heading)
            self._heading_text = None
            self._heading_level = None
        elif tag in self._boundaries:
            self._flush(bullet=tag == "li")
            if tag == "li":
                self._list_depth = max(0, self._list_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._ignored_stack:
            return
        if self._heading_text is not None:
            self._heading_text.append(data)
        else:
            self._buffer.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self, *, bullet: bool = False) -> None:
        text = _normalize_text("".join(self._buffer))
        self._buffer.clear()
        if text:
            if bullet:
                text = "• " + text
            self.segments.append(_Segment(text, self.section_path, self.source_block))


def _normalize_text(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").replace("\u200b", "").split()).strip()


def _segments_for_block(block: SourceBlock) -> list[_Segment]:
    parser = _BlockParser(block.block_id)
    parser.feed(block.content or "")
    parser.close()
    if parser.segments:
        return parser.segments
    # Heading-only embed blocks carry useful section text and lineage even if
    # their body is stored in another embed row. Keep the heading as text.
    if parser._heading_stack:
        parents = parser._heading_stack[:-1]
        return [
            _Segment(
                text=parser._heading_stack[-1],
                section_path=" › ".join(parents) if parents else None,
                source_block=block.block_id,
            )
        ]
    # Empty markup is a real empty block and should not create a vector.
    return []


def _input_text(title: str, text: str) -> str:
    return f"passage: {title.strip()}\n{text.strip()}"


def _encode_ids(tokenizer: Tokenizer, text: str, *, add_special_tokens: bool) -> list[int]:
    """Count and split through the fast tokenizer backend without length warnings."""
    backend = getattr(tokenizer, "backend_tokenizer", None)
    if backend is not None:
        return list(backend.encode(text, add_special_tokens=add_special_tokens).ids)
    return tokenizer.encode(text, add_special_tokens=add_special_tokens)


def _token_count(tokenizer: Tokenizer, title: str, text: str) -> int:
    return len(_encode_ids(tokenizer, _input_text(title, text), add_special_tokens=True))


def _decode(tokenizer: Tokenizer, tokens: list[int]) -> str:
    try:
        return tokenizer.decode(
            tokens, skip_special_tokens=True, clean_up_tokenization_spaces=False
        ).strip()
    except TypeError:
        return tokenizer.decode(tokens).strip()


def _split_oversized(
    segment: _Segment, *, title: str, tokenizer: Tokenizer, max_tokens: int
) -> list[_Segment]:
    sentences = [part for part in re.split(r"(?<=[.!?…])\s+", segment.text) if part]
    if len(sentences) > 1:
        groups: list[_Segment] = []
        pending: list[str] = []
        for sentence in sentences:
            candidate = " ".join((*pending, sentence))
            contextual = (
                f"{segment.section_path}\n\n{candidate}"
                if segment.section_path
                else candidate
            )
            if pending and _token_count(tokenizer, title, contextual) > max_tokens:
                groups.append(_Segment(" ".join(pending), segment.section_path, segment.source_block))
                pending = []
            pending.append(sentence)
            single = " ".join(pending)
            contextual = (
                f"{segment.section_path}\n\n{single}"
                if segment.section_path
                else single
            )
            if _token_count(tokenizer, title, contextual) > max_tokens:
                oversized = pending.pop()
                groups.extend(
                    _split_token_window(
                        _Segment(oversized, segment.section_path, segment.source_block),
                        title=title,
                        tokenizer=tokenizer,
                        max_tokens=max_tokens,
                    )
                )
        if pending:
            groups.append(_Segment(" ".join(pending), segment.section_path, segment.source_block))
        return groups
    return _split_token_window(segment, title=title, tokenizer=tokenizer, max_tokens=max_tokens)


def _split_token_window(
    segment: _Segment, *, title: str, tokenizer: Tokenizer, max_tokens: int
) -> list[_Segment]:
    ids = _encode_ids(tokenizer, segment.text, add_special_tokens=False)
    if not ids:
        return []
    # Start with a full window and shrink against the exact rendered input.
    # The section heading is repeated as context in render([segment]), so it
    # must count toward the budget just like the title and E5 prefix.
    window = max_tokens
    start = 0
    pieces: list[_Segment] = []
    while start < len(ids):
        size = min(window, len(ids) - start)
        while True:
            text = _decode(tokenizer, ids[start : start + size])
            rendered = f"{segment.section_path}\n\n{text}" if segment.section_path else text
            if text and _token_count(tokenizer, title, rendered) <= max_tokens:
                break
            if size <= 1:
                raise ValueError(
                    "Title and passage prefix leave no room for a content token under max_tokens"
                )
            size = max(1, size - max(1, size // 10))
        pieces.append(_Segment(text, segment.section_path, segment.source_block))
        start += size
    return pieces


def chunk_material(
    material: MaterialInput,
    tokenizer: Tokenizer,
    *,
    max_tokens: int = 512,
    target_tokens: int = 400,
) -> tuple[ChunkInput, ...]:
    """Clean and chunk one permitted material, preserving source block IDs.

    The entire short material is emitted as one chunk if its complete E5 input
    fits. Longer materials are packed at heading/paragraph/list boundaries and
    only split inside a semantic block when that block alone is too long.
    """
    if max_tokens < 8 or target_tokens < 8 or target_tokens > max_tokens:
        raise ValueError("Require 8 <= target_tokens <= max_tokens")
    if not material.answer_allowed:
        return ()
    if material.access_type != "free":
        raise ValueError("Knowledge chunks require an explicitly free material")
    title = _normalize_text(material.title)
    if not title:
        raise ValueError("Material title must not be empty")

    segments: list[_Segment] = []
    for block in sorted(material.blocks, key=lambda item: (item.order, item.block_id)):
        segments.extend(_segments_for_block(block))
    if not segments:
        return ()

    def render(items: list[_Segment]) -> str:
        output: list[str] = []
        last_path: str | None = None
        for item in items:
            if item.section_path and item.section_path != last_path:
                output.append(item.section_path)
                last_path = item.section_path
            output.append(item.text)
        return "\n\n".join(output).strip()

    full_text = render(segments)
    if _token_count(tokenizer, title, full_text) <= max_tokens:
        lineage = tuple(dict.fromkeys(s.source_block for s in segments))
        return (
            ChunkInput(
                chunk_no=0,
                text=full_text,
                section_path=next((s.section_path for s in segments if s.section_path), None),
                source_blocks=lineage,
                token_count=_token_count(tokenizer, title, full_text),
            ),
        )

    safe_segments: list[_Segment] = []
    for segment in segments:
        if _token_count(tokenizer, title, render([segment])) <= max_tokens:
            safe_segments.append(segment)
        else:
            safe_segments.extend(
                _split_oversized(segment, title=title, tokenizer=tokenizer, max_tokens=max_tokens)
            )

    chunks: list[ChunkInput] = []
    pending: list[_Segment] = []
    pending_path: str | None = None

    def flush() -> None:
        nonlocal pending, pending_path
        if not pending:
            return
        text = render(pending)
        count = _token_count(tokenizer, title, text)
        if count > max_tokens:
            raise AssertionError("Chunking emitted an oversized embedding input")
        chunks.append(
            ChunkInput(
                chunk_no=len(chunks),
                text=text,
                section_path=pending_path,
                source_blocks=tuple(dict.fromkeys(s.source_block for s in pending)),
                token_count=count,
            )
        )
        pending = []
        pending_path = None

    for segment in safe_segments:
        candidate = pending + [segment]
        candidate_count = _token_count(tokenizer, title, render(candidate))
        if pending and (segment.section_path != pending_path or candidate_count > target_tokens):
            flush()
            candidate = [segment]
            candidate_count = _token_count(tokenizer, title, render(candidate))
        # A single semantic block may exceed the target but never the hard cap.
        if candidate_count > max_tokens:
            raise AssertionError("A semantic segment exceeded the hard input limit")
        pending = candidate
        if pending_path is None:
            pending_path = segment.section_path
    flush()
    return tuple(chunks)

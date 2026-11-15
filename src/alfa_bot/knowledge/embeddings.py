"""Local multilingual-e5-small embeddings with strict input and shape checks."""

from __future__ import annotations

import math
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .contracts import ChunkInput

MODEL_NAME = "intfloat/multilingual-e5-small"
EMBEDDING_DIM = 384
MAX_INPUT_TOKENS = 512


class E5Embedder:
    """Lazy SentenceTransformers adapter; never downloads unless explicitly allowed.

    The model is loaded only when first used. `local_files_only=True` is the
    default so ingestion fails clearly instead of fetching model files silently.
    """

    def __init__(
        self,
        model_name: str = MODEL_NAME,
        *,
        revision: str | None = None,
        batch_size: int = 32,
        device: str | None = None,
        local_files_only: bool = True,
        model: Any | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.model_name = model_name
        self.requested_revision = revision or os.getenv("ALFA_EMBEDDING_REVISION") or "local-cache"
        self.batch_size = batch_size
        self.device = device
        self.local_files_only = local_files_only
        self._model = model
        self._resolved_revision: str | None = None
        if model is not None:
            candidate = getattr(model, "revision", None)
            if isinstance(candidate, str) and candidate:
                self._resolved_revision = candidate
            elif revision and revision != "local-cache":
                self._resolved_revision = revision

    @property
    def resolved_revision(self) -> str:
        """The immutable commit actually used to load both model and tokenizer."""
        if self._resolved_revision is None:
            _ = self.model
        if self._resolved_revision is None:
            raise RuntimeError(
                "Embedding model has no resolved immutable revision; set "
                "ALFA_EMBEDDING_REVISION to a cached commit"
            )
        return self._resolved_revision

    @property
    def model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "Embedding dependencies are missing; install sentence-transformers and torch"
                ) from exc
            snapshot, commit = self._resolve_snapshot()
            kwargs: dict[str, Any] = {"model_kwargs": {"local_files_only": True}}
            if self.device:
                kwargs["device"] = self.device
            try:
                self._model = SentenceTransformer(str(snapshot), **kwargs)
                self._resolved_revision = commit
            except Exception as exc:
                if self.local_files_only:
                    raise RuntimeError(
                        f"Local model {self.model_name!r} at revision {commit!r} "
                        "is unavailable; install/cache it explicitly"
                    ) from exc
                raise
        return self._model

    def _resolve_snapshot(self) -> tuple[Path, str]:
        """Resolve aliases to the cached/downloaded immutable snapshot first."""
        repo_dir_name = "models--" + self.model_name.replace("/", "--")
        cache_root = os.getenv("HF_HUB_CACHE")
        if not cache_root:
            configured_home = os.getenv("HF_HOME")
            if configured_home:
                cache_root = str(Path(configured_home).expanduser() / "hub")
            else:
                project_cache = Path.cwd() / "models" / "huggingface" / "hub"
                default_cache = Path("~/.cache/huggingface/hub").expanduser()
                cache_root = str(project_cache if project_cache.is_dir() else default_cache)
        repo_dir = Path(cache_root) / repo_dir_name
        requested = self.requested_revision
        resolved = requested
        if requested == "local-cache":
            ref = repo_dir / "refs" / "main"
            if ref.is_file():
                resolved = ref.read_text(encoding="utf-8").strip()
            else:
                snapshots = sorted((repo_dir / "snapshots").glob("*"))
                if len(snapshots) == 1:
                    resolved = snapshots[0].name
                elif not self.local_files_only:
                    resolved = "main"
                else:
                    raise RuntimeError(
                        f"Cannot resolve local-cache for {self.model_name!r}: "
                        "no main ref or unique cached snapshot"
                    )
        elif not (repo_dir / "snapshots" / requested).is_dir():
            ref = repo_dir / "refs" / requested
            if ref.is_file():
                resolved = ref.read_text(encoding="utf-8").strip()

        snapshot = repo_dir / "snapshots" / resolved
        if snapshot.is_dir():
            return snapshot, resolved
        if self.local_files_only:
            raise RuntimeError(
                f"Revision {requested!r} for {self.model_name!r} is not present in the local cache"
            )
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise RuntimeError("Install huggingface_hub to download an embedding snapshot") from exc
        downloaded = Path(
            snapshot_download(
                repo_id=self.model_name,
                revision=requested,
                cache_dir=cache_root,
                local_files_only=False,
            )
        )
        commit = downloaded.name
        if not commit or not (downloaded / "modules.json").is_file():
            raise RuntimeError("Downloaded model snapshot is missing SentenceTransformers metadata")
        return downloaded, commit

    @property
    def tokenizer(self) -> Any:
        tok = getattr(self.model, "tokenizer", None)
        if tok is None:
            raise RuntimeError("Loaded embedding model does not expose a tokenizer")
        return tok

    @staticmethod
    def passage_input(title: str, text: str) -> str:
        return f"passage: {title.strip()}\n{text.strip()}"

    def _check_input(self, text: str) -> None:
        count = len(self.tokenizer.encode(text, add_special_tokens=True))
        if count > MAX_INPUT_TOKENS:
            raise ValueError(f"E5 input has {count} tokens; maximum is {MAX_INPUT_TOKENS}")
        if count == 0:
            raise ValueError("E5 input must not be empty")

    def _encode(self, inputs: Sequence[str]) -> list[list[float]]:
        if not inputs:
            return []
        for value in inputs:
            self._check_input(value)
        raw = self.model.encode(
            list(inputs),
            batch_size=self.batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        rows = raw.tolist() if hasattr(raw, "tolist") else raw
        result: list[list[float]] = []
        for row in rows:
            values = [float(x) for x in row]
            if len(values) != EMBEDDING_DIM:
                raise ValueError(f"Expected {EMBEDDING_DIM} embedding values, got {len(values)}")
            if not all(math.isfinite(x) for x in values):
                raise ValueError("Embedding contains NaN or infinity")
            result.append(values)
        if len(result) != len(inputs):
            raise ValueError("Embedding model returned a different number of vectors than inputs")
        return result

    def encode_passages(self, title: str, chunks: Sequence[ChunkInput]) -> list[list[float]]:
        """Encode one material's chunks together in batches using `passage:`."""
        return self.encode_passage_batch([(title, chunk) for chunk in chunks])

    def encode_passage_batch(self, items: Sequence[tuple[str, ChunkInput]]) -> list[list[float]]:
        """Encode chunks from multiple materials in shared model batches.

        The returned vector order matches the `(title, chunk)` input order, so
        callers can zip this result back to the source records without losing
        chunk-to-material lineage.
        """
        return self._encode([self.passage_input(title, chunk.text) for title, chunk in items])

    def encode_query(self, text: str) -> list[float]:
        """Encode a retrieval query with the matching E5 `query:` prefix."""
        clean = text.strip()
        if not clean:
            raise ValueError("Query must not be empty")
        return self._encode([f"query: {clean}"])[0]

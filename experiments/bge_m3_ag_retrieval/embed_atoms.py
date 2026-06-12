#!/usr/bin/env python3
"""Embed HMAC atoms with BGE-M3."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np


HERE = Path(__file__).resolve().parent
DEFAULT_ATOMS = HERE / "out/atoms_hmac.jsonl"
DEFAULT_OUT = HERE / "out/embeddings_hmac.npz"


def _load_atoms(path: Path) -> list[dict]:
    atoms: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                atoms.append(json.loads(line))
    return atoms


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    denom = np.linalg.norm(mat, axis=1, keepdims=True)
    denom[denom == 0.0] = 1.0
    return mat / denom


def _encode(model_name: str, texts: list[str], batch_size: int, fp16: bool) -> np.ndarray:
    try:
        from FlagEmbedding import BGEM3FlagModel
    except ImportError as exc:
        raise SystemExit(
            "FlagEmbedding is not installed. Run: pip install -r requirements.txt"
        ) from exc

    model = BGEM3FlagModel(model_name, use_fp16=fp16)
    result = model.encode(
        texts,
        batch_size=batch_size,
        max_length=8192,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )
    dense = result["dense_vecs"]
    return np.asarray(dense, dtype=np.float32)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--atoms", type=Path, default=DEFAULT_ATOMS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--model", default="BAAI/bge-m3")
    parser.add_argument("--hf-home", type=Path, default=HERE / "out/hf_cache")
    parser.add_argument("--online-download-model", action="store_true")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--no-fp16", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("HF_HOME", str(args.hf_home))
    if not args.online_download_model:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    atoms = _load_atoms(args.atoms)
    if not atoms:
        raise SystemExit(f"No atoms found in {args.atoms}")

    texts = [atom["embedding_text"] for atom in atoms]
    emb = _l2_normalize(_encode(args.model, texts, args.batch_size, not args.no_fp16))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        args.out,
        embeddings=emb,
        atom_ids=np.array([atom["atom_id"] for atom in atoms]),
        model=np.array([args.model]),
    )
    print(f"Wrote embeddings {emb.shape} to {args.out}")


if __name__ == "__main__":
    main()

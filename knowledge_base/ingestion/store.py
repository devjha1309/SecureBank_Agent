"""Local hashed bag-of-words embeddings and cosine retrieval; policy text only."""

import hashlib
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def embed(text: str) -> list[float]:
    vector = [0.0] * 256
    for word in re.findall(r"[a-z]{3,}", text.lower()):
        if word not in {"the", "and", "for", "your", "what", "does", "this", "with", "how", "are"}:
            vector[int(hashlib.sha256(word.encode()).hexdigest()[:8], 16) % 256] += 1
    norm = math.sqrt(sum(x * x for x in vector)) or 1
    return [x / norm for x in vector]


def ingest() -> list[dict]:
    chunks = []
    for path in sorted((ROOT / "documents").glob("*.json")):
        doc = json.loads(path.read_text())
        for n, text in enumerate(doc["text"].split("\n\n")):
            clean = " ".join(text.split())
            chunks.append(
                {
                    **{k: v for k, v in doc.items() if k != "text"},
                    "section": str(n + 1),
                    "text": clean,
                    "embedding": embed(doc["title"] + " " + clean),
                }
            )
    target = ROOT / "index.json"
    target.write_text(json.dumps(chunks, indent=2))
    return chunks


def search(query: str) -> list[dict]:
    path = ROOT / "index.json"
    chunks = json.loads(path.read_text()) if path.exists() else ingest()
    vector = embed(query)
    ranked = sorted(
        ((sum(a * b for a, b in zip(vector, c["embedding"])), c) for c in chunks),
        key=lambda pair: pair[0],
        reverse=True,
    )
    return [
        {**{k: v for k, v in c.items() if k != "embedding"}, "score": round(score, 3)}
        for score, c in ranked[:3]
        if score >= 0.18
    ]


if __name__ == "__main__":
    print(f"Ingested {len(ingest())} synthetic policy chunks.")

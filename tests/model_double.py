"""Deterministic test-only transport double; not a semantic embedding or real LLM."""
import hashlib
import re
from app.models import TEST_PROFILE, normalize
from app.ingestion import ENCODING


class TestProvider:
    profile = TEST_PROFILE

    def embed(self, texts):
        vectors = []
        for text in texts:
            vector = [0.0] * 1536
            normalized = re.sub(r"\s", "", text.lower())
            for gram in [normalized[i:i+2] for i in range(max(1, len(normalized)-1))]:
                index = int.from_bytes(hashlib.sha256(gram.encode()).digest()[:4], "big") % 1536
                vector[index] += 1
            vectors.append(normalize(vector))
        return vectors, sum(len(ENCODING.encode(text, disallowed_special=())) for text in texts), 0

    def answer(self, question, sources):
        source = sources[0] if sources else None
        return {"evidence": [{"source_id": source["source_id"], "quote": source["text"][:200]}] if source else []}, 500, 100

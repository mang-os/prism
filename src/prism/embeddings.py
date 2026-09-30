import hashlib
import json
from collections import OrderedDict
from pathlib import Path
from threading import Lock

import numpy as np

from prism.errors import PrismError

MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"


def download_model(path, revision=None):
    from huggingface_hub import HfApi, snapshot_download

    path = Path(path)
    revision = revision or HfApi().model_info(MODEL_ID).sha
    snapshot_download(
        MODEL_ID,
        revision=revision,
        local_dir=str(path),
        allow_patterns=["*.json", "*.txt", "*.safetensors", "*.model", "1_Pooling/*"],
        ignore_patterns=["onnx/*", "openvino/*"],
    )
    metadata = {
        "model_id": MODEL_ID,
        "revision": revision,
        "template": "title-newline-body-v1",
        "normalization": "unit-l2",
        "dimension": 384,
    }
    path.mkdir(parents=True, exist_ok=True)
    (path / "prism-model.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


class LocalEmbeddings:
    def __init__(self, path):
        self.path = Path(path)
        if not (self.path / "prism-model.json").exists():
            raise PrismError("SEMANTIC_UNAVAILABLE", "Run prism model download first", 503)
        try:
            import torch
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise PrismError(
                "SEMANTIC_UNAVAILABLE", f"Cannot load embedding libraries: {e}", 503
            ) from e
        self.metadata = json.loads((self.path / "prism-model.json").read_text())
        torch.set_num_threads(2)
        self.model = SentenceTransformer(str(self.path), local_files_only=True, device="cpu")
        dimension_method = getattr(self.model, "get_embedding_dimension", None)
        self.dimension = (dimension_method or self.model.get_sentence_embedding_dimension)()
        if self.dimension != self.metadata["dimension"]:
            raise PrismError("MODEL_MISMATCH", "Model dimension does not match metadata", 409)
        self.fingerprint = hashlib.sha256(
            json.dumps(self.metadata, sort_keys=True).encode()
        ).hexdigest()
        self.cache = OrderedDict()
        self.lock = Lock()
        self.truncated_documents = 0

    def encode_documents(self, texts):
        with self.lock:
            self.truncated_documents += sum(
                len(self.model.tokenizer(t, truncation=False)["input_ids"])
                > self.model.max_seq_length
                for t in texts
            )
            result = self.model.encode(
                texts,
                batch_size=32,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        return np.asarray(result, dtype=np.float32)

    def encode_query(self, text):
        key = hashlib.sha256(text.encode()).hexdigest()
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key].copy()
            result = np.asarray(
                self.model.encode(
                    [text],
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                )[0],
                dtype=np.float32,
            )
            self.cache[key] = result
            if len(self.cache) > 1024:
                self.cache.popitem(last=False)
            return result.copy()

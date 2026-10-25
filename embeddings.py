"""CPU for online retrieval; CUDA only in the standalone indexing worker."""

from config import EMBED_MODEL


def load_encoder(device="cpu"):
    import torch
    from sentence_transformers import SentenceTransformer
    if device == "cpu":
        torch.set_num_threads(min(4, torch.get_num_threads()))
    model = SentenceTransformer(EMBED_MODEL, device=device)
    model.max_seq_length = 512
    return model

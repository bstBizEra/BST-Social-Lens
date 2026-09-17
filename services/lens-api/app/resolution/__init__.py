"""Phase 7 (SLL-PROP-DATA-001E) pure building blocks: canonical permalinks, Lao-safe text similarity, MATCH_V1 scorer."""
from .match import MATCH_VERSION, Side, score_pair
from .permalink import PERMALINK_RULES_VERSION, canonical_permalink, post_identity
from .textsim import jaccard_3gram, simhash64, hamming

__all__ = ["MATCH_VERSION", "Side", "score_pair", "PERMALINK_RULES_VERSION", "canonical_permalink", "post_identity",
           "jaccard_3gram", "simhash64", "hamming"]

"""Phase 7 (SLL-PROP-DATA-001E) pure building blocks: canonical permalinks, Lao-safe text similarity, MATCH_V1 scorer."""
from .blocking import BLOCKING_VERSION, BlockingResult, block
from .cluster import CLUSTER_VERSION, Cluster, ClusterInput, PairEvidence, build_clusters, pair_evidence
from .match import MATCH_VERSION, Side, score_pair
from .permalink import PERMALINK_RULES_VERSION, canonical_permalink, post_identity
from .textsim import jaccard_3gram, simhash64, hamming

__all__ = ["BLOCKING_VERSION", "BlockingResult", "block", "CLUSTER_VERSION", "Cluster", "ClusterInput", "PairEvidence", "build_clusters", "pair_evidence",
           "MATCH_VERSION", "Side", "score_pair", "PERMALINK_RULES_VERSION", "canonical_permalink", "post_identity",
           "jaccard_3gram", "simhash64", "hamming"]

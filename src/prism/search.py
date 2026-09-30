import hashlib
import heapq
import math
import re
import uuid
from collections import defaultdict

import numpy as np

from prism.analysis import TEXT_FIELDS, terms
from prism.deadline import Deadline
from prism.errors import PrismError
from prism.expansion import BKTree, GramIndex, Synonyms, protected
from prism.models import SearchRequest
from prism.query import Executor, Parser, filter_matches, positive_clauses
from prism.telemetry import LATENCY, REQUESTS, logger, stage


def ranked(scores, k):
    return heapq.nsmallest(k, scores.items(), key=lambda x: (-x[1], x[0]))


def fuse(lexical, semantic, top_k=10, constant=60):
    scores: dict[str, float] = defaultdict(float)
    ranks: dict[str, dict[str, int]] = {}
    for branch, results in (("lexical", lexical), ("semantic", semantic)):
        for rank, (external, _) in enumerate(results, 1):
            scores[external] += 1 / (constant + rank)
            ranks.setdefault(external, {})[branch] = rank
    return ranked(scores, top_k), ranks


class SearchEngine:
    def __init__(self, index, provider=None, synonyms=None, global_stats=None):
        self.index, self.provider = index, provider
        self.synonyms = Synonyms(synonyms)
        self.global_stats = global_stats
        self.tree_generation, self.tree = None, None
        self.vocabulary_cache: dict[str, set[str]] = {}

    def _plain_groups(self, request, snapshot):
        vocabulary_stats = self.global_stats or snapshot.stats
        vocabulary = self.vocabulary_cache.get(snapshot.generation)
        if vocabulary is None:
            vocabulary = {t for s in vocabulary_stats.values() for t in s["df"] if len(t) <= 64}
            self.vocabulary_cache[snapshot.generation] = vocabulary
            if len(self.vocabulary_cache) > 2:
                self.vocabulary_cache.pop(next(iter(self.vocabulary_cache)))
        groups, expansion_count, limited = [], 0, False
        # Quotes suppress expansion; the plain mode never interprets DSL operators.
        for piece in re.findall(r'"[^"]*"|[^"\s]+(?:\s+[^"\s]+)*', request.q):
            quoted = piece.startswith('"') and piece.endswith('"')
            token_list = terms(piece)
            if quoted:
                if token_list:
                    groups.append([(" ".join(token_list), None, True, 1.0, "original")])
                continue
            i = 0
            while i < len(token_list):
                token = token_list[i]
                source = None
                if request.synonyms:
                    matches = [
                        r for r in self.synonyms.rules if tuple(token_list[i : i + len(r)]) == r
                    ]
                    source = max(matches, key=len) if matches else None
                group = [(" ".join(source) if source else token, None, False, 1.0, "original")]
                if source:
                    for alt in self.synonyms.rules[source][:4]:
                        if expansion_count >= 32:
                            limited = True
                            break
                        group.append((" ".join(alt), None, len(alt) > 1, 0.8, "synonym"))
                        expansion_count += 1
                elif (
                    request.typo_tolerance
                    and token not in vocabulary
                    and len(token) >= 4
                    and not protected(token)
                ):
                    if self.tree_generation != snapshot.generation:
                        self.tree = (
                            BKTree(vocabulary) if len(vocabulary) <= 5000 else GramIndex(vocabulary)
                        )
                        self.tree_generation = snapshot.generation
                    assert self.tree is not None
                    variants, cut, _ = self.tree.search(token, 1 if len(token) <= 7 else 2)
                    limited |= cut
                    frequencies = {
                        t: sum(s["df"].get(t, 0) for s in vocabulary_stats.values())
                        for t, _ in variants
                    }
                    variants.sort(key=lambda v: (v[1], -frequencies[v[0]], v[0]))
                    for alt, _ in variants[:8]:
                        if expansion_count >= 32:
                            limited = True
                            break
                        group.append((alt, None, False, 0.7, "fuzzy"))
                        expansion_count += 1
                if group not in groups:
                    groups.append(group)
                i += len(source) if source else 1
        return groups, limited

    def branches(self, request, *, query_vector=None, deadline=None):
        request = (
            request if isinstance(request, SearchRequest) else SearchRequest.model_validate(request)
        )
        deadline = deadline or Deadline(request.timeout_ms)
        snapshot = self.index.snapshot
        executor = Executor(snapshot, deadline)
        timings: dict[str, float] = {}
        mode = request.mode or ("hybrid" if snapshot.config["dimension"] else "lexical")
        requested_mode = mode
        degraded, limited = False, False
        with stage("parse", timings):
            if len(terms(request.q)) > 64:
                raise PrismError("QUERY_TOO_COMPLEX", "At most 64 analyzed query terms")
            ast = Parser(request.q).parse() if request.query_mode == "dsl" else None
            clauses = positive_clauses(ast) if ast else []
            dense_text = " ".join(c[0] for c in clauses) if ast else request.q
            if mode != "lexical" and not terms(dense_text):
                raise PrismError("QUERY_TYPE_ERROR", "Semantic DSL requires positive text clauses")
        with stage("eligibility", timings):
            eligible = set(snapshot.docs)
            for f in request.filters:
                deadline.check()
                eligible = {
                    d for d in eligible if filter_matches(snapshot.docs[d], f.field, f.op, f.value)
                }
            if ast:
                eligible = executor.evaluate(ast, eligible=eligible)
        with stage("expansion", timings):
            if ast:
                groups = [
                    [(value, field, phrase, 1.0, "original")]
                    for value, field, phrase in dict.fromkeys(clauses)
                ]
            else:
                groups, limited = self._plain_groups(request, snapshot)
            if not groups and not ast:
                raise PrismError("QUERY_TYPE_ERROR", "No searchable text")
        if mode != "lexical":
            compatible = snapshot.config["dimension"] and (
                query_vector is not None
                or (
                    self.provider is not None
                    and self.provider.fingerprint == snapshot.config["fingerprint"]
                )
            )
            if not compatible:
                if request.allow_lexical_fallback:
                    mode, degraded = "lexical", True
                else:
                    raise PrismError(
                        "SEMANTIC_UNAVAILABLE", "Compatible local embeddings required", 503
                    )
        lexical_scores: dict[str, float] = defaultdict(float)
        explanations: dict[str, list[dict]] = defaultdict(list)
        with stage("postings", timings):
            if mode in ("lexical", "hybrid"):
                if not groups:
                    lexical_scores.update({d: 0.0 for d in eligible})
                for group in groups:
                    best: dict[str, float] = {}
                    best_explanations: dict[str, list[dict]] = {}
                    for value, scope, phrase, weight, source in group:
                        fields = (scope,) if scope else TEXT_FIELDS
                        matches = executor.phrase(value, fields, eligible) if phrase else eligible
                        contribution: dict[str, float] = defaultdict(float)
                        detail: dict[str, list[dict]] = defaultdict(list)
                        for field in fields:
                            corpus = (self.global_stats or snapshot.stats)[field]
                            n = corpus["N"]
                            avg = corpus["total_length"] / n if n else 1
                            for term in dict.fromkeys(terms(value)):
                                df = corpus["df"].get(term, 0)
                                idf = math.log(1 + (n - df + 0.5) / (df + 0.5)) if df else 0
                                posting = snapshot.matches(field, term, deadline, eligible=matches)
                                for external, tf in posting.items():
                                    deadline.check()
                                    length = snapshot.field_lengths[external][field]
                                    field_weight = 2.0 if field == "title" else 1.0
                                    score = (
                                        weight
                                        * field_weight
                                        * idf
                                        * tf
                                        * 2.2
                                        / (tf + 1.2 * (0.25 + 0.75 * length / avg))
                                    )
                                    contribution[external] += score
                                    if request.explain:
                                        detail[external].append(
                                            {
                                                "term": term,
                                                "field": field,
                                                "tf": tf,
                                                "df": df,
                                                "field_length": length,
                                                "avg_length": avg,
                                                "idf": idf,
                                                "source": source,
                                                "weight": weight * field_weight,
                                                "contribution": score,
                                            }
                                        )
                        for external, score in contribution.items():
                            if external not in best or score > best[external]:
                                best[external], best_explanations[external] = (
                                    score,
                                    detail[external],
                                )
                    for external, score in best.items():
                        lexical_scores[external] += score
                        explanations[external].extend(best_explanations[external])
                if ast:
                    for external in eligible:
                        lexical_scores.setdefault(external, 0.0)
        lexical = ranked(lexical_scores, request.candidate_k)
        semantic = []
        if mode in ("semantic", "hybrid"):
            with stage("embedding", timings):
                deadline.check()
                query_vector = (
                    self.provider.encode_query(dense_text)
                    if query_vector is None
                    else np.asarray(query_vector, dtype=np.float32)
                )
                if (
                    query_vector.shape != (snapshot.config["dimension"],)
                    or not np.isfinite(query_vector).all()
                    or np.linalg.norm(query_vector) < 1e-6
                ):
                    raise PrismError("INVALID_VECTOR", "Invalid query embedding", 422)
                query_vector = query_vector / np.linalg.norm(query_vector)
                deadline.check()
            with stage("vector", timings):
                scores: dict[str, float] = {}
                ids = sorted(eligible)
                for i in range(0, len(ids), 512):
                    deadline.check()
                    block = ids[i : i + 512]
                    similarities = np.stack([snapshot.vectors[d] for d in block]) @ query_vector
                    scores.update(zip(block, map(float, similarities), strict=True))
                semantic = ranked(scores, request.candidate_k)
        deadline.check()
        return {
            "lexical": lexical,
            "semantic": semantic,
            "mode": mode,
            "requested_mode": requested_mode,
            "degraded": degraded,
            "expansion_limited": limited,
            "explanations": dict(explanations),
            "dense_text": dense_text,
            "timings_ms": timings,
            "generation": snapshot.generation,
            "documents": {d: snapshot.docs[d] for d in set(dict(lexical)) | set(dict(semantic))},
            "ast": ast.to_dict() if ast else None,
        }

    def search(self, request):
        request = (
            request if isinstance(request, SearchRequest) else SearchRequest.model_validate(request)
        )
        deadline = Deadline(request.timeout_ms)
        request_id = uuid.uuid4().hex
        mode = request.mode or ("hybrid" if self.index.config["dimension"] else "lexical")
        try:
            branches = self.branches(request, deadline=deadline)
            if branches["mode"] == "hybrid":
                hits, ranks = fuse(branches["lexical"], branches["semantic"], request.top_k)
            else:
                hits, ranks = branches[branches["mode"]][: request.top_k], {}
            response = {
                "request_id": request_id,
                "requested_mode": branches["requested_mode"],
                "executed_mode": branches["mode"],
                "took_ms": deadline.elapsed_ms(),
                "partial": False,
                "degraded": branches["degraded"],
                "snapshot_epoch": branches["generation"],
                "coverage": {"successful": 1, "total": 1},
                "shards": [{"id": 0, "status": "ok", "replica": "local", "attempts": 1}],
                "candidate_k": request.candidate_k,
                "expansion_limited": branches["expansion_limited"],
                "hits": [],
                "warnings": ["lexical_fallback"] if branches["degraded"] else [],
            }
            for external, score in hits:
                explanation = None
                if request.explain:
                    explanation = {
                        "bm25": branches["explanations"].get(external, []),
                        "branch_ranks": ranks.get(external, {}),
                        "cosine": dict(branches["semantic"]).get(external),
                        "rrf_constant": 60,
                        "dense_text": branches["dense_text"],
                        "model_fingerprint": self.index.config["fingerprint"],
                        "ast": branches["ast"],
                        "timings_ms": branches["timings_ms"],
                    }
                response["hits"].append(
                    {
                        "id": external,
                        "score": score,
                        "document": branches["documents"][external],
                        "explanation": explanation,
                    }
                )
            REQUESTS.labels(mode, "ok").inc()
            if deadline.elapsed_ms() > 200:
                logger.info(
                    "slow_query %s",
                    {
                        "request_id": request_id,
                        "query_hash": hashlib.sha256(request.q.encode()).hexdigest(),
                        "timings_ms": branches["timings_ms"],
                    },
                )
            return response
        except PrismError:
            REQUESTS.labels(mode, "error").inc()
            raise
        finally:
            LATENCY.labels(mode).observe(deadline.elapsed_ms() / 1000)

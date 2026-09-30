import json
from collections import defaultdict

from prism.analysis import terms
from prism.errors import PrismError


def distance(a, b):
    row = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        nxt = [i]
        for j, right in enumerate(b, 1):
            nxt.append(min(nxt[-1] + 1, row[j] + 1, row[j - 1] + (left != right)))
        row = nxt
    return row[-1]


class BKTree:
    def __init__(self, vocabulary):
        self.root = None
        for word in sorted(vocabulary):
            if self.root is None:
                self.root = (word, {})
                continue
            node = self.root
            while (d := distance(word, node[0])) in node[1]:
                node = node[1][d]
            if d:
                node[1][d] = (word, {})

    def search(self, word, radius, max_nodes=2000):
        stack = [self.root] if self.root else []
        out, visited = [], 0
        while stack and visited < max_nodes:
            node = stack.pop()
            d = distance(word, node[0])
            visited += 1
            if d <= radius:
                out.append((node[0], d))
            stack.extend(
                child for edge, child in sorted(node[1].items()) if d - radius <= edge <= d + radius
            )
        return out, bool(stack), visited


class GramIndex:
    """Bounded bigram candidate lookup followed by exact edit-distance checks."""

    def __init__(self, vocabulary):
        self.words = sorted(vocabulary)
        postings = defaultdict(list)
        for index, word in enumerate(self.words):
            for gram in self._grams(word):
                postings[gram].append(index)
        self.postings = dict(postings)

    @staticmethod
    def _grams(word):
        padded = "^" + word + "$"
        return {padded[i : i + 2] for i in range(len(padded) - 1)}

    def search(self, word, radius, max_nodes=2000):
        overlap: dict[int, int] = defaultdict(int)
        for gram in self._grams(word):
            for index in self.postings.get(gram, ()):
                candidate = self.words[index]
                if abs(len(candidate) - len(word)) <= radius:
                    overlap[index] += 1
        ordered = sorted(overlap, key=lambda i: (-overlap[i], self.words[i]))
        limited = len(ordered) > max_nodes
        results = []
        for index in ordered[:max_nodes]:
            candidate = self.words[index]
            edit = distance(word, candidate)
            if edit <= radius:
                results.append((candidate, edit))
        return results, limited, min(len(ordered), max_nodes)


class Synonyms:
    def __init__(self, path=None):
        self.version = "none"
        self.rules: dict[tuple[str, ...], list[tuple[str, ...]]] = {}
        if path is None:
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        self.version = data["version"]
        if len(data["rules"]) > 1000:
            raise PrismError("SYNONYM_CONFIG_ERROR", "Too many synonym rules")
        for rule in data["rules"]:
            source = tuple(terms(rule["from"]))
            if not source or len(source) > 16 or rule["direction"] not in ("one_way", "equivalent"):
                raise PrismError("SYNONYM_CONFIG_ERROR", "Invalid synonym rule")
            if not rule["to"] or len(rule["to"]) > 4:
                raise PrismError("SYNONYM_CONFIG_ERROR", "Expected 1–4 alternatives")
            alternatives = [tuple(terms(t)) for t in rule["to"]]
            if any(not t or len(t) > 16 for t in alternatives):
                raise PrismError("SYNONYM_CONFIG_ERROR", "Empty or oversized alternative")
            self.rules.setdefault(source, []).extend(alternatives)
            if rule["direction"] == "equivalent":
                for alternative in alternatives:
                    self.rules.setdefault(alternative, []).append(source)


def protected(term):
    return any(c.isdigit() for c in term) or len(term) > 64

"""Small typed DSL, strict eligibility, and inspectable execution planning."""

import json
import math
import re
from dataclasses import asdict, dataclass
from dataclasses import field as dataclass_field

from prism.analysis import KEYWORD_FIELDS, NUMERIC_FIELDS, TEXT_FIELDS, normalize, terms
from prism.errors import PrismError


@dataclass(frozen=True)
class Node:
    kind: str
    value: str = ""
    field: str | None = None
    op: str = "eq"
    children: tuple["Node", ...] = dataclass_field(default_factory=tuple)
    span: tuple[int, int] = (0, 0)

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Lexeme:
    kind: str
    value: str
    start: int
    end: int


PATTERN = re.compile(
    r'\s+|(?P<phrase>"(?:\\.|[^"\\])*")|(?P<punct>\(|\)|:)|(?P<comp>>=|<=|>|<|=)|(?P<word>[^\s():"<>=]+)'
)


def lex(text):
    out, offset = [], 0
    while offset < len(text):
        m = PATTERN.match(text, offset)
        if m is None:
            raise PrismError(
                "QUERY_SYNTAX_ERROR", "Unexpected character or unclosed quote", start=offset
            )
        if not m[0].isspace():
            kind = m.lastgroup
            assert kind is not None
            value = m[0]
            if kind == "phrase":
                if re.search(r'\\[^"\\]', value):
                    raise PrismError(
                        "QUERY_SYNTAX_ERROR",
                        "Only quote and backslash escapes are supported",
                        start=offset,
                    )
                value = json.loads(value)
            if kind == "word" and value in ("AND", "OR", "NOT"):
                kind = value
            out.append(Lexeme(kind, value, m.start(), m.end()))
        offset = m.end()
    out.append(Lexeme("EOF", "", len(text), len(text)))
    if len(out) > 200:
        raise PrismError("QUERY_TOO_COMPLEX", "Too many DSL tokens")
    return out


class Parser:
    def __init__(self, text):
        self.tokens, self.i, self.depth = lex(text), 0, 0

    @property
    def token(self):
        return self.tokens[self.i]

    def take(self):
        token = self.token
        if token.kind == "EOF":
            self.error("Unexpected end of query")
        self.i += 1
        return token

    def error(self, message):
        raise PrismError("QUERY_SYNTAX_ERROR", message, start=self.token.start, end=self.token.end)

    def parse(self):
        node = self.or_expr()
        if self.token.kind != "EOF":
            self.error("Unexpected trailing token")
        return node

    def or_expr(self):
        nodes = [self.and_expr()]
        while self.token.kind == "OR":
            self.take()
            nodes.append(self.and_expr())
        return nodes[0] if len(nodes) == 1 else Node("or", children=tuple(nodes))

    def and_expr(self):
        nodes = [self.unary()]
        while self.token.kind not in ("OR", "EOF") and self.token.value != ")":
            if self.token.kind == "AND":
                self.take()
            nodes.append(self.unary())
        return nodes[0] if len(nodes) == 1 else Node("and", children=tuple(nodes))

    def unary(self):
        self.depth += 1
        if self.depth > 16:
            raise PrismError("QUERY_TOO_COMPLEX", "AST nesting exceeds 16")
        if self.token.kind == "NOT":
            token = self.take()
            node = Node("not", children=(self.unary(),), span=(token.start, self.token.start))
        else:
            node = self.primary()
        self.depth -= 1
        return node

    def primary(self):
        token = self.take()
        if token.value == "(":
            node = self.or_expr()
            if self.token.value != ")":
                self.error("Expected closing parenthesis")
            self.take()
            return node
        if token.kind not in ("word", "phrase"):
            self.error("Expected term, phrase, field, or group")
        if self.token.value == ":":
            if token.value not in (*TEXT_FIELDS, *KEYWORD_FIELDS, *NUMERIC_FIELDS):
                raise PrismError(
                    "QUERY_TYPE_ERROR", "Unknown field", field=token.value, start=token.start
                )
            self.take()
            if self.token.value == "(":
                if token.value not in TEXT_FIELDS:
                    raise PrismError("QUERY_TYPE_ERROR", "Only text fields accept groups")
                child = self.primary()
                if has_field(child):
                    raise PrismError("QUERY_TYPE_ERROR", "Nested field scopes are unsupported")
                return Node("scope", field=token.value, children=(child,))
            op = "eq"
            if self.token.kind == "comp":
                op = {"=": "eq", ">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}[self.take().value]
            value = self.take()
            if value.kind not in ("word", "phrase"):
                self.error("Expected field value")
            if token.value in NUMERIC_FIELDS:
                try:
                    numeric = float(value.value)
                    if not math.isfinite(numeric):
                        raise ValueError
                except ValueError as e:
                    raise PrismError(
                        "QUERY_TYPE_ERROR", "Numeric filter requires finite number"
                    ) from e
                return Node("filter", value=value.value, field=token.value, op=op)
            if op != "eq":
                raise PrismError("QUERY_TYPE_ERROR", "Range comparisons require numeric fields")
            if token.value in KEYWORD_FIELDS:
                return Node("filter", value=value.value, field=token.value)
            return text_node(value, token.value)
        return text_node(token)


def text_node(token, text_field=None):
    analyzed = terms(token.value)
    if not analyzed:
        raise PrismError("QUERY_TYPE_ERROR", "Text clause has no analyzed tokens")
    if token.kind == "phrase":
        return Node("phrase", " ".join(analyzed), text_field, span=(token.start, token.end))
    nodes = tuple(Node("term", t, text_field, span=(token.start, token.end)) for t in analyzed)
    return nodes[0] if len(nodes) == 1 else Node("and", children=nodes)


def has_field(node):
    return node.field is not None or any(has_field(c) for c in node.children)


def positive_clauses(node, scope=None, negated=False):
    if node.kind == "not":
        return positive_clauses(node.children[0], scope, not negated)
    if node.kind == "scope":
        return positive_clauses(node.children[0], node.field, negated)
    if node.kind in ("term", "phrase") and not negated:
        return [(node.value, node.field or scope, node.kind == "phrase")]
    return [x for c in node.children for x in positive_clauses(c, scope, negated)]


def filter_matches(doc, name, op, value):
    actual = doc.get(name)
    if actual is None:
        return False
    if name in NUMERIC_FIELDS:
        number = float(value)
        return {
            "eq": actual == number,
            "gt": actual > number,
            "gte": actual >= number,
            "lt": actual < number,
            "lte": actual <= number,
        }[op]
    values = [normalize(x) for x in actual] if isinstance(actual, list) else [normalize(actual)]
    wanted = [normalize(x) for x in value] if isinstance(value, list) else [normalize(value)]
    return bool(set(values) & set(wanted))


class Executor:
    def __init__(self, snapshot, deadline):
        self.snapshot, self.deadline = snapshot, deadline

    def phrase(self, value, fields, eligible=None):
        result = set()
        for f in fields:
            postings = [
                self.snapshot.matches(f, t, self.deadline, positions=True, eligible=eligible)
                for t in terms(value)
            ]
            candidates = set.intersection(*(set(p) for p in postings)) if postings else set()
            for external in candidates:
                self.deadline.check()
                shifted = [{p - i for p in post[external]} for i, post in enumerate(postings)]
                if set.intersection(*shifted):
                    result.add(external)
        return result

    def estimate(self, node):
        if node.kind == "term":
            return (
                sum(self.snapshot.stats[f]["df"].get(node.value, 0) for f in (node.field,) if f)
                if node.field
                else sum(s["df"].get(node.value, 0) for s in self.snapshot.stats.values())
            )
        return len(self.snapshot.docs)

    def evaluate(self, node, scope=None, eligible=None):
        self.deadline.check()
        universe = set(self.snapshot.docs) if eligible is None else eligible
        fields = (node.field or scope,) if node.field or scope else TEXT_FIELDS
        if node.kind == "term":
            return set().union(
                *(
                    set(self.snapshot.matches(f, node.value, self.deadline, eligible=universe))
                    for f in fields
                )
            )
        if node.kind == "phrase":
            return self.phrase(node.value, fields, universe)
        if node.kind == "filter":
            return {
                d
                for d in universe
                if filter_matches(self.snapshot.docs[d], node.field, node.op, node.value)
            }
        if node.kind == "scope":
            return self.evaluate(node.children[0], node.field, universe)
        if node.kind == "not":
            return universe - self.evaluate(node.children[0], scope, universe)
        if node.kind == "and":
            for c in sorted(node.children, key=self.estimate):
                universe &= self.evaluate(c, scope, universe)
                if not universe:
                    break
            return universe
        if node.kind == "or":
            return set().union(*(self.evaluate(c, scope, universe) for c in node.children))
        raise AssertionError(node.kind)

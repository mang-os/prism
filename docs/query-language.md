# Query language

`query_mode=plain` is the default. Words are ordinary text; `AND`, `OR`, and colons are not operators. Keyword retrieval uses OR-style scoring while semantic retrieval searches independently. A quoted span contributes a phrase-scored lexical alternative and suppresses expansion for those tokens. Structured filters always constrain eligibility.

`query_mode=dsl` parses exact constraints. Precedence is `NOT` > `AND` > `OR`; adjacent clauses imply AND. Parentheses group. An unfielded term/phrase searches title or body. `title:` and `body:` scope text. `kind`, `category`, and `tags` compare normalized keywords; `price` and `rating` accept finite numeric comparisons. A pure negative or filter-only lexical query is allowed and orders zero-scored matches by external ID.

```text
"distributed systems" AND reliability
title:(database OR storage)
rating:>=4 AND NOT kind:product
(database OR storage) AND NOT beginner
```

Quote/backslash escaping is supported in phrases. Text field groups cannot nest conflicting scopes. Field predicates, phrases, Boolean exclusions, and structured filters are hard constraints in every retrieval mode. For DSL semantic/hybrid requests, the embedding input uses only positive text clauses; pure filter/negative DSL queries require lexical mode. Unknown fields and malformed syntax return typed errors with a source span where available.

Fuzzy expansion is one edit for absent words of length 4–7, two edits for words 8+, and disabled for numeric/identifier tokens and quoted spans. It visits at most 2,000 BK-tree nodes per token on small vocabularies; on vocabularies over 5,000 terms it checks at most 2,000 bigram candidates with exact edit distance. It proposes at most eight variants per word and shares a 32-alternative budget with synonyms. Curated rules expand once and can produce field-local phrase alternatives. Alternatives score at lower weights and cannot widen a DSL predicate. The bigram path is approximate and sets `expansion_limited` when candidates are cut.

Limits: query <=2,048 characters, <=64 analyzed terms, AST depth <=16, <=32 structured filters, `top_k<=100`, `candidate_k<=1,000`, and `candidate_k>=top_k`. Search budget is 10–5,000 ms, default 500 ms. See the build spec for exact grammar, BM25 formula, and scoring groups.


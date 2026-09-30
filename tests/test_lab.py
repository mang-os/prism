import pytest

from prism.lab import metrics, render_html


def test_known_metrics():
    result = metrics(["bad", "good", "good"], {"good": 2})
    assert result["mrr@10"] == 0.5
    assert result["ndcg@10"] == pytest.approx(1 / __import__("math").log2(3))
    assert result["recall@10"] == 1
    assert result["success@5"] == 1
    assert not any(metrics(["bad"], {}).values())


def test_html_escapes_source_content(tmp_path):
    path = tmp_path / "report.html"
    render_html(
        {
            "metadata": {},
            "summary": {},
            "runs": {
                "example": [
                    {
                        "query_id": "q",
                        "text": "<script>alert(1)</script>",
                        "metrics": {"ndcg@10": 0},
                        "hits": [],
                    }
                ]
            },
        },
        path,
    )
    assert "<script>alert(1)</script>" not in path.read_text()
    assert "&lt;script&gt;" in path.read_text()

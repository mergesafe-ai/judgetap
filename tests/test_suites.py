import json

import pytest

import judgetap as sj
from judgetap import suites
from judgetap.evaluate import load_cases, main

NAMES = ["World", "Sports", "Business", "Sci/Tech"]


def fake_api(total, calls):
    def get(url):
        calls.append(url)
        offset = int(url.split("offset=")[1].split("&")[0])
        rows = [
            {"row_idx": i, "row": {"text": f"article {i}", "label": i % 4}}
            for i in range(offset, min(offset + suites.PAGE, total))
        ]
        return {
            "features": [
                {"name": "text", "type": {"dtype": "string"}},
                {"name": "label", "type": {"names": NAMES, "_type": "ClassLabel"}},
            ],
            "rows": rows,
            "num_rows_total": total,
        }

    return get


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGETAP_SUITES_DIR", str(tmp_path))
    return tmp_path


def test_registry_records_licence_and_source():
    assert set(suites.REGISTRY) >= {"ag_news", "banking77"}
    for s in suites.REGISTRY.values():
        assert s.licence and s.source.startswith("https://huggingface.co/")


def test_download_pages_converts_and_caches(cache, monkeypatch):
    calls = []
    monkeypatch.setattr(suites, "_get_json", fake_api(250, calls))
    path = suites.suite_path("ag_news")
    assert len(calls) == 3 and "dataset=fancyzhx%2Fag_news" in calls[0]
    assert path.parent == cache
    cases = load_cases(path)
    assert len(cases) == 250
    assert cases[1].label == "Sports" and cases[1].context == "article 1"
    assert list(cases[0].question.options) == NAMES
    suites.suite_path("ag_news")  # cached: no further requests
    assert len(calls) == 3


def test_failed_download_leaves_no_cache(cache, monkeypatch):
    def boom(url):
        raise OSError("offline")

    monkeypatch.setattr(suites, "_get_json", boom)
    with pytest.raises(sj.JudgetapError, match="offline"):
        suites.suite_path("banking77")
    assert list(cache.iterdir()) == []


def test_unknown_suite_lists_available():
    with pytest.raises(sj.JudgetapError, match="ag_news, banking77"):
        suites.suite_path("nope")


def test_sample_is_deterministic_and_ordered():
    items = list(range(1000))
    a, b = suites.sample(items, 10), suites.sample(items, 10)
    assert a == b == sorted(a) and len(set(a)) == 10
    assert suites.sample(items, None) is items
    assert suites.sample(items, 5000) is items
    with pytest.raises(sj.JudgetapError):
        suites.sample(items, 0)


def test_cli_suite_with_limit(cache, monkeypatch, capsys):
    from judgetap import engines
    from judgetap.testing import StaticEngine

    monkeypatch.setattr(suites, "_get_json", fake_api(40, []))
    monkeypatch.setattr(
        engines,
        "load",
        lambda spec: StaticEngine(lambda q, c: {"World": 1.0}, name=spec),
    )
    assert main(["--suite", "ag_news", "--limit", "7", "--engines", "e", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)[0]
    assert report["cases"] == 7


def test_cli_needs_exactly_one_source(tmp_path):
    with pytest.raises(SystemExit):
        main(["--engines", "x"])
    with pytest.raises(SystemExit):
        main([str(tmp_path / "c.jsonl"), "--suite", "ag_news", "--engines", "x"])

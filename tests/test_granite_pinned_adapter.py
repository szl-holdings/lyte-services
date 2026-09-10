"""Pinned adapter contract tests; all model operations are test doubles."""

import sys
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from lyte.intelligence.granite_timeseries import (
    MODEL_ID,
    GraniteAdapterError,
    GranitePatchTSTProvider,
    _quantile_column,
)

TEST_REVISION = "a" * 40  # Deliberately synthetic; not an admitted model revision.


@pytest.mark.parametrize("revision", ["", "main", "v2", "abc123", "g" * 40, "a" * 39])
def test_mutable_or_malformed_revisions_are_rejected(revision):
    with pytest.raises(GraniteAdapterError, match="immutable"):
        GranitePatchTSTProvider(revision=revision)


def test_revision_can_be_selected_through_environment(monkeypatch):
    monkeypatch.setenv("LYTE_GRANITE_REVISION", TEST_REVISION.upper())
    provider = GranitePatchTSTProvider()
    assert provider.revision == TEST_REVISION
    assert f"{MODEL_ID}@{TEST_REVISION}" in provider.name
    assert "context=512;frequency=1h" in provider.name


def test_explicit_bad_revision_does_not_fall_back_to_environment(monkeypatch):
    monkeypatch.setenv("LYTE_GRANITE_REVISION", TEST_REVISION)
    with pytest.raises(GraniteAdapterError):
        GranitePatchTSTProvider(revision="main")


@pytest.mark.parametrize("context", [True, 15, 8193, 32.5])
def test_context_is_a_bounded_integer(context):
    with pytest.raises(GraniteAdapterError):
        GranitePatchTSTProvider(revision=TEST_REVISION, context_length=context)


def test_foreign_checkpoint_is_not_silently_attributed_to_ibm():
    with pytest.raises(GraniteAdapterError):
        GranitePatchTSTProvider(revision=TEST_REVISION, model_id="test/other")


def test_lazy_load_passes_exact_revision_and_reuses_model(monkeypatch):
    calls = []
    model = SimpleNamespace(config=SimpleNamespace(context_length=8192))

    def load(model_id, **kwargs):
        calls.append((model_id, kwargs))
        return model

    module = SimpleNamespace(PatchTSTFMForPrediction=SimpleNamespace(from_pretrained=load))
    monkeypatch.setitem(sys.modules, "tsfm_public", module)
    provider = GranitePatchTSTProvider(revision=TEST_REVISION)
    assert provider._load() is model
    assert provider._load() is model
    assert calls == [(MODEL_ID, {"revision": TEST_REVISION})]


@pytest.mark.parametrize("columns,q,expected", [
    (["value_0.1"], 0.1, "value_0.1"),
    (["value_q0.5"], 0.5, "value_q0.5"),
    (["q0.9"], 0.9, "q0.9"),
    (["value_0.101", "value_0.104"], 0.104, "value_0.104"),
])
def test_exact_quantile_column_mapping(columns, q, expected):
    assert _quantile_column(columns, q) == expected


@pytest.mark.parametrize("columns,q", [
    (["unrelated_0.5"], 0.5),
    (["value_0.5", "q0.5"], 0.5),
    (["value_0.1"], 0.100000001),
    (["value_0.5", "value_0.5"], 0.5),
])
def test_suffix_ambiguity_duplicates_and_rounded_aliases_are_rejected(columns, q):
    with pytest.raises(GraniteAdapterError):
        _quantile_column(columns, q)


@pytest.fixture
def fake_pipeline(monkeypatch):
    # pandas is optional in the core application. These tests run wherever the
    # Granite lane dependencies exist; no real weights are fetched or inferred.
    pd = pytest.importorskip("pandas")
    observed = {}

    class Pipeline:
        def __init__(self, **kwargs):
            observed["config"] = kwargs

        def __call__(self, frame):
            observed["frame"] = frame
            config = observed["config"]
            return pd.DataFrame({
                f"value_{q}": [float(q)] * config["prediction_length"]
                for q in config["quantile_levels"]
            })

    monkeypatch.setitem(sys.modules, "tsfm_public", SimpleNamespace(
        TimeSeriesForecastingPipeline=Pipeline,
        PatchTSTFMForPrediction=SimpleNamespace(
            from_pretrained=lambda *a, **kw: SimpleNamespace(
                config=SimpleNamespace(context_length=8192)
            )
        ),
    ))
    return observed


@pytest.mark.parametrize("frequency,seconds", [("15min", 900), ("1h", 3600), ("1D", 86400)])
def test_synthetic_index_matches_configured_frequency(fake_pipeline, frequency, seconds):
    provider = GranitePatchTSTProvider(revision=TEST_REVISION, frequency=frequency)
    result = provider.forecast([float(i) for i in range(32)], horizon=2, quantiles=(0.1, 0.5, 0.9))
    timestamps = fake_pipeline["frame"]["timestamp"]
    assert (timestamps.iloc[1] - timestamps.iloc[0]).total_seconds() == seconds
    assert fake_pipeline["config"]["freq"] == frequency
    assert len(result) == 2
    assert result[0] == {0.1: 0.1, 0.5: 0.5, 0.9: 0.9}


@pytest.mark.parametrize("frequency", ["-1h", "0h", "1ME", "not-a-frequency"])
def test_nonregular_or_nonpositive_frequency_fails_before_inference(fake_pipeline, frequency):
    provider = GranitePatchTSTProvider(revision=TEST_REVISION, frequency=frequency)
    with pytest.raises(GraniteAdapterError, match="sampling interval"):
        provider.forecast([1.0] * 32, horizon=2, quantiles=(0.1, 0.5, 0.9))
    assert fake_pipeline == {}


def test_provider_identity_cannot_drift_after_construction():
    provider = GranitePatchTSTProvider(revision=TEST_REVISION)
    with pytest.raises(FrozenInstanceError):
        provider.revision = "b" * 40

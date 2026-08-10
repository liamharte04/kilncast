"""Every numeric parameter in the curves file must carry value, unit, source."""

from pathlib import Path

import yaml

CURVES = Path(__file__).resolve().parent.parent / "curves" / "rivan-v1.yaml"
VALID_SOURCES = {"rivan-h2", "rivan-dac", "rivan-gsmr", "rivan-home", "literature", "chemistry", "assumed"}


def _params(node, path=""):
    """Yield (path, dict) for every leaf parameter dict."""
    if isinstance(node, dict):
        if "value" in node:
            yield path, node
        else:
            for key, child in node.items():
                yield from _params(child, f"{path}.{key}")


def test_all_params_tagged():
    data = yaml.safe_load(CURVES.read_text())
    params = list(_params(data))
    assert len(params) > 30, "curves file looks too thin"
    for path, p in params:
        assert "unit" in p, f"{path} missing unit"
        assert "source" in p, f"{path} missing source"
        src = p["source"].split(",")[0].split(" ")[0]
        assert src in VALID_SOURCES, f"{path} has unknown source tag {p['source']!r}"


def test_stoichiometry_is_chemistry():
    data = yaml.safe_load(CURVES.read_text())
    for _, p in _params(data["stoichiometry"]):
        assert p["source"].startswith("chemistry")

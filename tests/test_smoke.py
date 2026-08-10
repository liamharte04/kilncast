from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_sites_manifest_parses():
    data = yaml.safe_load((ROOT / "sites.yaml").read_text())
    sites = data["sites"]
    assert len(sites) >= 15
    for site in sites:
        assert -90 <= site["lat"] <= 90
        assert -180 <= site["lon"] <= 180
        assert site["country"]


def test_packages_import():
    import control  # noqa: F401
    import sim  # noqa: F401

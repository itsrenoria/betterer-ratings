from __future__ import annotations

from pathlib import Path


def test_compose_uses_stable_container_name_and_rotates_logs():
    compose_text = (Path(__file__).resolve().parents[2] / "compose.yaml").read_text(
        encoding="utf-8"
    )

    assert "container_name: betterer-ratings" in compose_text
    assert (
        "image: ${BETTERER_IMAGE:-ghcr.io/itsrenoria/betterer-ratings:latest}"
        in compose_text
    )
    assert "driver: json-file" in compose_text
    assert 'max-size: "25m"' in compose_text
    assert 'max-file: "4"' in compose_text

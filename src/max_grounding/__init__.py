"""MAX Grounding public package surface."""

from __future__ import annotations

__version__ = "0.0.1"


def project_identity() -> dict[str, object]:
    """Return immutable bootstrap identity used by smoke/E2E acceptance."""
    return {
        "name": "max-grounding",
        "version": __version__,
        "cross_platform": True,
        "interfaces": ("python",),
    }

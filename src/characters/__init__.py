"""Character builders organized by sinner."""

from src.characters.base import Character
from . import (
    don_quixote,
    faust,
    gregor,
    heathcliff,
    hong_lu,
    ishmael,
    meursault,
    outis,
    rodion,
    ryoshu,
    sinclair,
    yi_sang,
)

_SINNER_PACKAGES = (
    yi_sang,
    faust,
    don_quixote,
    ryoshu,
    meursault,
    hong_lu,
    heathcliff,
    ishmael,
    rodion,
    sinclair,
    outis,
    gregor,
)

# Re-export every builder exposed by each sinner package.
for _pkg in _SINNER_PACKAGES:
    for _name in getattr(_pkg, "__all__", []):
        globals()[_name] = getattr(_pkg, _name)

__all__ = ["Character"]
for _pkg in _SINNER_PACKAGES:
    __all__.append(_pkg.__name__.split(".")[-1])
    __all__.extend(getattr(_pkg, "__all__", []))

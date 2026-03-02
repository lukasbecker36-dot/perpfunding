"""Application configuration: env vars, paths, spot mapping, symbol helpers."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@dataclass
class Settings:
    DB_PATH: str = os.getenv("DB_PATH", "out/history.sqlite")
    OUT_DIR: str = "out"
    LORIS_URL: str = "https://api.loris.tools/funding"
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    spot_mapping: dict = field(default_factory=dict)


def _load_spot_mapping() -> dict:
    """Load config/spot_mapping.yaml relative to project root."""
    candidates = [
        Path(__file__).parent.parent / "config" / "spot_mapping.yaml",
        Path("config") / "spot_mapping.yaml",
    ]
    for p in candidates:
        if p.exists():
            with p.open() as f:
                data = yaml.safe_load(f) or {}
            return {k.upper(): v for k, v in data.items()}
    _log.warning("spot_mapping.yaml not found; spot prices will be unavailable")
    return {}


def _init_out_dir(out_dir: str) -> None:
    Path(out_dir).mkdir(parents=True, exist_ok=True)


# Module-level singleton
settings = Settings(spot_mapping=_load_spot_mapping())
_init_out_dir(settings.OUT_DIR)

# ---------------------------------------------------------------------------
# Symbol normalization
# ---------------------------------------------------------------------------

_SUFFIX_RE = re.compile(
    r"(-USDT|-USDTM|-PERP|-USD|_USDT|_USD|USDTM|USDT|USD|-SWAP)$",
    re.IGNORECASE,
)


def normalize_symbol(raw: str) -> str:
    """Strip exchange-specific suffixes and uppercase.

    Examples:
        BTC-USDT  -> BTC
        XBTUSDTM  -> XBT   (KuCoin BTC alias — callers map XBT->BTC if needed)
        ETH-PERP  -> ETH
        solusdt   -> SOL
    """
    cleaned = _SUFFIX_RE.sub("", raw.strip()).upper()
    # KuCoin uses XBT for BTC
    if cleaned == "XBT":
        return "BTC"
    return cleaned

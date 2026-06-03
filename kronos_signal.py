#!/usr/bin/env python3
"""
Kronos AI Signal — wrapper around the Kronos-mini foundation model.

Returns a forecast direction for a given stock:
  +1  = model predicts price HIGHER in next 5 days
  -1  = model predicts price LOWER in next 5 days
   0  = neutral / model unavailable

Kronos-mini is loaded once and cached for the process lifetime.
If PyTorch or the model weights are not available, returns 0 (neutral)
and logs a warning — the bot continues normally without this signal.

Install dependencies (one-time, on your Mac):
    pip3 install torch torchvision --index-url https://download.pytorch.org/whl/cpu
    pip3 install huggingface_hub transformers

Model is downloaded automatically on first run (~130 MB for mini).
"""
import os
import sys
import logging
import warnings

log = logging.getLogger("kronos")

# ── Add kronos_model/ to path so imports work ─────────────────────
DIR = os.path.dirname(os.path.abspath(__file__))
KRONOS_MODEL_DIR = os.path.join(DIR, "kronos_model")
if KRONOS_MODEL_DIR not in sys.path:
    sys.path.insert(0, DIR)   # needed for relative imports inside kronos_model/

_predictor = None          # module-level cache — load once, reuse
_load_attempted = False    # don't retry on repeated import failures


def _load_kronos():
    """Load Kronos-mini from HuggingFace Hub. Returns KronosPredictor or None."""
    global _predictor, _load_attempted
    if _load_attempted:
        return _predictor
    _load_attempted = True

    try:
        import torch
        # Suppress verbose transformers/HF warnings
        warnings.filterwarnings("ignore", category=UserWarning)
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

        # Add kronos_model directory for internal imports
        if KRONOS_MODEL_DIR not in sys.path:
            sys.path.insert(0, KRONOS_MODEL_DIR)

        # Import Kronos classes from the local model directory
        from kronos_model import KronosTokenizer, Kronos, KronosPredictor  # type: ignore

        log.info("Loading Kronos-mini tokenizer from HuggingFace Hub...")
        tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-2k")
        log.info("Loading Kronos-mini model from HuggingFace Hub...")
        model = Kronos.from_pretrained("NeoQuasar/Kronos-mini")
        model.eval()

        device = "cuda" if torch.cuda.is_available() else "cpu"
        _predictor = KronosPredictor(model, tokenizer, max_context=512)
        log.info(f"Kronos-mini loaded successfully (device={device})")
        return _predictor

    except ImportError as e:
        log.warning(f"Kronos unavailable — PyTorch not installed: {e}")
        log.warning("Run: pip3 install torch --index-url https://download.pytorch.org/whl/cpu")
        return None
    except Exception as e:
        log.warning(f"Kronos failed to load: {e}")
        return None


def get_kronos_signal(hist_df) -> int:
    """
    Given a yfinance history DataFrame (OHLCV, daily bars), return:
      +1  if Kronos forecasts price will be higher in 5 days
      -1  if lower
       0  if neutral or model unavailable

    hist_df: pandas DataFrame with columns Open/High/Low/Close/Volume
             (as returned by yf.Ticker(...).history(period='1y'))
    """
    predictor = _load_kronos()
    if predictor is None:
        return 0

    try:
        import pandas as pd

        # Kronos expects lowercase column names
        df = hist_df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.columns = ["open", "high", "low", "close", "volume"]
        df = df.dropna()

        if len(df) < 60:
            log.warning("Kronos: not enough history (need ≥60 bars)")
            return 0

        # Use up to 400 bars of lookback (Kronos-mini context = 2048)
        lookback = min(400, len(df) - 5)
        pred_len = 5   # forecast 5 trading days ahead

        df = df.reset_index(drop=True)
        x_df = df.iloc[:lookback][["open", "high", "low", "close", "volume"]]

        # Build timestamp Series — Kronos needs datetime index
        # We'll use business day offsets from today
        import numpy as np
        last_date = pd.Timestamp.today().normalize()
        x_ts = pd.bdate_range(end=last_date, periods=lookback)
        y_ts = pd.bdate_range(start=last_date + pd.offsets.BDay(1), periods=pred_len)

        x_timestamp = pd.Series(x_ts)
        y_timestamp = pd.Series(y_ts)

        pred_df = predictor.predict(
            df=x_df,
            x_timestamp=x_timestamp,
            y_timestamp=y_timestamp,
            pred_len=pred_len,
            T=1.0,
            top_p=0.9,
            sample_count=3,   # average 3 samples for stability
            verbose=False,
        )

        current_close = float(df["close"].iloc[lookback - 1])
        forecast_close = float(pred_df["close"].mean())

        pct_change = (forecast_close - current_close) / current_close * 100
        log.info(f"Kronos forecast: current={current_close:.2f}  "
                 f"5d_avg={forecast_close:.2f}  change={pct_change:+.2f}%")

        if pct_change > 0.5:
            return 1
        elif pct_change < -0.5:
            return -1
        else:
            return 0

    except Exception as e:
        log.warning(f"Kronos signal error: {e}")
        return 0

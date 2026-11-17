"""Live input path == research input path: the flow book must see exactly what the model was trained/tested on.
python -m pytest -q bensim/test_flow_live.py  (needs data/edge_miner.pkl and data/bars/fxcm)"""
import pickle

import numpy as np
import pandas as pd
import pytest

from bensim import edge_miner as em
from bensim import flow_live as fl
from bensim.fxcm_history import load


@pytest.mark.parametrize("when", ["2025-06-10 14:00", "2026-03-03 09:00"])
def test_live_inputs_equal_research_rows(when):
    t_dec = pd.Timestamp(when)
    frames = []
    for s in fl.UNIVERSE:  # what the live engine does: the last 700 completed H1 bars of every pair
        m = load(s, "H1")
        k = np.flatnonzero(m["t"] + 3600 <= t_dec.timestamp())[-700:]
        frames.append(em.features(s, {c: m[c][k] for c in ("t", "high", "low", "close", "spr")}))
    live = em.add_strength(pd.concat(frames, ignore_index=True))
    live = fl.add_gaps(live[live.t == t_dec].copy()).set_index("symbol").loc[fl.PAIRS]
    with open(em.PATH, "rb") as fh:  # our own cache
        ref = pickle.load(fh)
    ref = fl.add_gaps(ref[ref.t == t_dec].copy()).set_index("symbol").loc[fl.PAIRS]
    diff = (live[fl.FEATS].astype(float) - ref[fl.FEATS].astype(float)).abs().max().max()
    assert diff < 1e-9, diff

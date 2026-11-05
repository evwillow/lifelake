"""Synthetic data for the demo: an invented person's year, written in the formats the connectors read.

Everything comes from the hand-set distributions in ``params.PARAMS`` and one ``random.Random(seed)``.
Nothing is fitted to real data. The same seed and scale give a byte-identical tree, file times included.

    from lifelake.synth import generate
    truth = generate("demo_data", seed=7)

``truth`` holds counts computed from the generated records, for comparison with SQL over the built lake.
"""
from __future__ import annotations

import random
import shutil
from pathlib import Path

from .claude import write_claude
from .core import Planter, Tree
from .params import PARAMS
from .spotify import write_spotify
from .takeout import write_takeout

__all__ = ["DEMO_TRAVEL", "DEMO_TZ", "PARAMS", "generate"]

DEMO_TZ = PARAMS["home_tz"]
DEMO_TRAVEL = f"{PARAMS['trip_tz']}:{PARAMS['trip_first']}..{PARAMS['trip_last']}"
_SUBDIRS = ("claude_export", "claude_projects", "takeout", "spotify")


def generate(out_dir, seed: int = 7, scale: float = 1.0, overwrite: bool = False,
             secrets_out: list[str] | None = None) -> dict:
    """Write the tree under ``out_dir`` and return ground-truth counts.

    ``overwrite`` first removes the four source folders; otherwise a non-empty ``out_dir`` is an error,
    since leftovers from another run would break reproducibility. ``secrets_out``, if given, receives the
    planted secret values, so a test can check that none of them reached the lake.
    """
    if scale <= 0:
        raise ValueError("scale must be positive")
    root = Path(out_dir)
    if overwrite:
        for name in _SUBDIRS:
            shutil.rmtree(root / name, ignore_errors=True)
    elif root.exists() and any(root.iterdir()):
        raise FileExistsError(f"{root} is not empty; pick a new folder or pass overwrite")
    root.mkdir(parents=True, exist_ok=True)

    # One child stream per source, so a change in one source leaves the others' draws alone.
    master = random.Random(seed)
    streams = {name: random.Random(master.getrandbits(64)) for name in ("plant", "ai", "code", "takeout", "spotify")}
    plant = Planter(streams["plant"], secrets_out)
    tree = Tree(root)

    truth: dict = {"seed": seed, "scale": scale}
    truth.update(write_claude(streams["ai"], streams["code"], tree, plant, scale))
    truth.update(write_takeout(streams["takeout"], tree, plant, scale))
    truth.update(write_spotify(streams["spotify"], tree, plant, scale))
    tree.finish()

    truth["secrets_planted"] = sum(plant.by_type.values())
    truth["secrets_by_type"] = dict(sorted(plant.by_type.items()))
    truth["secrets_by_field"] = dict(sorted(plant.by_field.items()))
    truth["secrets_by_source"] = plant.by_source()
    truth["events_total"] = sum(truth[k] for k in (
        "takeout_search", "takeout_youtube", "takeout_maps", "takeout_chrome", "takeout_location", "takeout_calendar",
        "takeout_email", "spotify_plays", "spotify_searches", "spotify_playlists", "spotify_inferences",
        "spotify_library"))
    return truth

"""Run the racing example as a dedicated server or network client."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "role",
    nargs="?",
    default="client",
    choices=("server", "client"),
)
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=7777)
args = parser.parse_args()

if args.role == "server":
    os.environ["G_HEADLESS_SERVER"] = "1"
    if (
        platform.system() == "Linux"
        and not os.environ.get("DISPLAY")
        and not os.environ.get("WAYLAND_DISPLAY")
    ):
        os.environ["PYGLET_HEADLESS"] = "true"

import pyglet

if args.role == "server":
    pyglet.options["audio"] = ("silent",)
    pyglet.options["shadow_window"] = False

from g import Game
from race import configure_race


ROOT = Path(__file__).resolve().parent
game = Game.from_project(
    ROOT / "g.project.json",
    network_role=args.role,
)
if args.role == "server":
    configure_race("multiplayer")
    game.scenes.load(
        "race",
        str(ROOT / "racing.scene.json"),
        asset_root=str(ROOT),
    )
    game.scenes.switch("race")
    port = game.network.listen(args.host, args.port)
    print(f"Racing server listening on {args.host}:{port}", flush=True)
    game.run_server()
else:
    configure_race("multiplayer", f"ws://{args.host}:{args.port}")
    game.scenes.load(
        "menu",
        str(ROOT / "menu.scene.json"),
        asset_root=str(ROOT),
    )
    game.scenes.switch("menu")
    game.run(title="Network Racer")

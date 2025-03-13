import logging
import os
import pathlib

import yaml
from celery import shared_task
from flask import request, g, Blueprint
from lib import lichess
from lib.play_game import handle_challenge, play_game
from lib.dual_zero_v04.config import get_config
from lib.mimic import MimicTestBot

dn = pathlib.Path(__file__).parent.resolve()

bp = Blueprint("bp", __name__, url_prefix="/")


def get_existing_games():
    if "games" not in g:
        g.games = set()
    return g.games


def get_bot_config():
    if "config" not in g:
        g.config = get_config(os.path.join(dn, "config.yml"))
    return g.config


def get_li():
    if "li" not in g:
        with open(os.path.join(dn, "lib", "versioning.yml")) as version_file:
            versioning_info = yaml.safe_load(version_file)
        __version__ = versioning_info["lichess_bot_version"]

        config = get_bot_config()
        logging_level = logging.INFO
        max_retries = config.engine.online_moves.max_retries

        g.li = lichess.Lichess(
            config.token, config.url, __version__, logging_level, max_retries
        )
    return g.li


def get_user_profile():
    if "user_profile" not in g:
        li = get_li()
        g.user_profile = li.get_profile()
    return g.user_profile


@shared_task(ignore_result=False, time_limit=30 * 60)
def handle_play_game(game_id: str, engine):
    play_game(
        game_id, get_li(), get_bot_config(), get_user_profile()["username"], engine
    )


@bp.get("/")
def say_hello():
    return "hello from mimicBot server"


@bp.post("/challenge")
def incoming_challenge():
    msg = request.json
    handle_challenge(msg, get_li(), get_bot_config().challenge, get_user_profile())
    return {"bot-server": "received challenge"}


def get_engine_id():
    if "engines" not in g:
        g.engines = [
            {"active": False, "id": i}
            for i in range(get_bot_config().challenge.concurrency)
        ]
    for rec in g.engines:
        if not rec["active"]:
            rec["active"] = True
            return rec
    else:
        return None


@bp.get("/gameStart/<gameId>")
def gameStart(gameId):
    games = get_existing_games()
    if gameId not in games:
        games.add(gameId)
        engine = get_engine_id()
        if engine is None:
            return {"bot-server": "max games exceeded"}
        else:
            handle_play_game.delay(gameId, engine)
            return {"bot-server": "received gameStart"}
    else:
        return {"bot-server": "ignored gameStart for " + gameId}

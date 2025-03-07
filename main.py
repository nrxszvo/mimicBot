import logging
import os
import pathlib

import yaml
from celery import shared_task
from flask import Flask, request
from flask_cors import CORS

from flask_factory import celery_init_app
from lib import lichess
from lib.play_game import handle_challenge, play_game
from lib.dual_zero_v04.config import get_config

dn = pathlib.Path(__file__).parent.resolve()
with open(os.path.join(dn, "lib", "versioning.yml")) as version_file:
    versioning_info = yaml.safe_load(version_file)
__version__ = versioning_info["lichess_bot_version"]

config = get_config(os.path.join(dn, "config.yml"))

# app = create_app()
app = Flask(__name__)
CORS(app)
app.config.from_mapping(
    CELERY=dict(
        broker_url="pyamqp://localhost",
        result_backend="rpc://localhost",
        task_ignore_result=True,
    ),
)
app.config.from_prefixed_env()
celery_app = celery_init_app(app)

logging_level = logging.INFO
max_retries = config.engine.online_moves.max_retries
max_games = config.challenge.concurrency

li = lichess.Lichess(config.token, config.url, __version__, logging_level, max_retries)
user_profile = li.get_profile()


@shared_task(ignore_result=False)
def handle_play_game(game_id: str):
    play_game(game_id, li, config, user_profile["username"])


@app.get("/")
def say_hello():
    return "hello from mimicBot server"


@app.post("/challenge")
def parse_request():
    msg = request.json
    handle_challenge(msg, li, config, user_profile)
    return {"state": "received challenge"}


@app.get("/gameStart/<gameId>")
def gameStart(gameId):
    play_game.delay(gameId)
    return {"state": "received gameStart"}


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

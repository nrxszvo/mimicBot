import logging
import os
import pathlib

import yaml
from celery import shared_task
from flask import Flask, request
from flask_cors import CORS

from flask_factory import celery_init_app
from lib import lichess
from lib.config import load_config
from lib.play_game import handle_challenge
from lib.play_game import play_game as call_play_game

dn = pathlib.Path(__file__).parent.resolve()
with open(os.path.join(dn, "lib", "versioning.yml")) as version_file:
    versioning_info = yaml.safe_load(version_file)

__version__ = versioning_info["lichess_bot_version"]

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

CONFIG = load_config(os.path.join(dn, "config.yml"))
logging_level = logging.INFO
max_retries = CONFIG.engine.online_moves.max_retries
max_games = CONFIG.challenge.concurrency

li = lichess.Lichess(CONFIG.token, CONFIG.url, __version__, logging_level, max_retries)
user_profile = li.get_profile()


@shared_task(ignore_result=False)
def play_game(game_id: str):
    call_play_game(game_id, li, CONFIG, user_profile["username"])


@app.get("/")
def say_hello():
    return "hello from mimicBot server"


@app.post("/challenge")
def parse_request():
    msg = request.json
    handle_challenge(msg, li, user_profile)
    return {"info": "received challenge"}


@app.get("/gameStart/<gameId>")
def gameStart(gameId):
    play_game.delay(gameId)
    return {"info": "received gameStart"}


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

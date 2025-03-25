import logging
import os
import pathlib
import io

import yaml
from celery import shared_task, Task
from flask import request, Flask
from flask_cors import CORS

from lib import lichess
from lib.dual_zero_v04.config import get_config
from lib.play_game import handle_challenge, play_game, analyze_pgn
from flask_factory import celery_init_app

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

dn = pathlib.Path(__file__).parent.resolve()

with open(os.path.join(dn, "lib", "versioning.yml")) as version_file:
    versioning_info = yaml.safe_load(version_file)
__version__ = versioning_info["lichess_bot_version"]

config = get_config(os.path.join(dn, "config.yml"))
logging_level = logging.INFO
max_retries = config.engine.online_moves.max_retries

li = lichess.Lichess(config.token, config.url, __version__, logging_level, max_retries)
user_profile = li.get_profile()

active_games = set()


class CallbackTask(Task):
    def on_success(self, retval, task_id, args, kwargs):
        """
        retval – The return value of the task.
        task_id – Unique id of the executed task.
        args – Original arguments for the executed task.
        kwargs – Original keyword arguments for the executed task.
        """
        active_games.remove(args[0])

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """
        exc – The exception raised by the task.
        task_id – Unique id of the failed task.
        args – Original arguments for the task that failed.
        kwargs – Original keyword arguments for the task that failed.
        """
        active_games.remove(args[0])


@shared_task(ignore_result=False, time_limit=30 * 60)
def handle_play_game(game_id: str):
    play_game(
        game_id,
        li,
        config,
        user_profile["username"],
    )


@app.get("/")
def say_hello():
    return "hello from mimicBot server"


@app.post("/challenge")
def incoming_challenge():
    msg = request.json
    if msg["challenge"]["id"] in active_games:
        return {
            "challenge": {
                "accepted": False,
                "decline_reason": msg["challenge"]["id"] + " exists",
            }
        }

    accepted, reason = handle_challenge(
        msg["challenge"], li, config.challenge, user_profile
    )
    return {"challenge": {"accepted": accepted, "decline_reason": reason}}


@app.get("/gameStart/<gameId>")
def gameStart(gameId):
    if gameId in active_games:
        return {"gameStart": {"accepted": False, "decline_reason": gameId + " exists"}}
    else:
        active_games.add(gameId)
        handle_play_game.delay(gameId)
        return {"gameStart": {"accepted": True}}


@app.post("/analyzePgn")
def analyzePgn():
    msg = request.json
    return analyze_pgn(io.StringIO(msg))


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

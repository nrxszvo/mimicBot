from celery import shared_task
from celery.result import AsyncResult

from requests.exceptions import (
    ChunkedEncodingError,
    HTTPError,
    ReadTimeout,
    ConnectionError,
)

from http.client import RemoteDisconnected
from flask_factory import celery_init_app
from flask import Flask, request
from flask_cors import CORS
from lib import lichess, model
from lib.config import load_config
from lib.timer import to_seconds, seconds, msec
from lib.mimic import MimicTestBot
import time
import chess
from chess.variant import find_variant
import itertools
import copy
import yaml
import logging
import json
import os

with open("lib/versioning.yml") as version_file:
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

CONFIG = load_config("./config.yml")
logging_level = logging.INFO
max_retries = CONFIG.engine.online_moves.max_retries
max_games = CONFIG.challenge.concurrency

li = lichess.Lichess(CONFIG.token, CONFIG.url, __version__, logging_level, max_retries)
user_profile = li.get_profile()


@app.get("/")
def say_hello():
    return "hello from mimicBot server"


@app.post("/challenge")
def parse_request():
    msg = request.json
    handle_challenge(msg)
    return {"info": "received challenge"}


@app.get("/gameStart/<gameId>")
def gameStart(gameId):
    play_game.delay(gameId)
    return {"info": "received gameStart"}


def game_is_active(li: lichess.Lichess, game_id: str) -> bool:
    """Determine if a game is still being played."""
    return game_id in (
        ongoing_game["gameId"] for ongoing_game in li.get_ongoing_games()
    )


def next_update(lines):
    """Get the next game state."""
    binary_chunk = next(lines)
    upd = json.loads(binary_chunk.decode("utf-8")) if binary_chunk else {}
    return upd


def is_game_over(game: model.Game) -> bool:
    """Check whether the game is over."""
    status = game.state["status"]
    return status != "started"


def game_changed(current_game: model.Game, prior_game) -> bool:
    """Check whether the current game state is different from the previous game state."""
    if prior_game is None:
        return True

    current_game_moves_str = current_game.state["moves"]
    prior_game_moves_str = prior_game.state["moves"]
    return current_game_moves_str != prior_game_moves_str


def bot_to_move(game: model.Game, board: chess.Board) -> bool:
    return game.is_white == (board.turn == chess.WHITE)


def is_engine_move(game, prior_game, board) -> bool:
    """Check whether it is the engine's turn."""
    return game_changed(game, prior_game) and bot_to_move(game, board)


def setup_board(game: model.Game) -> chess.Board:
    """Set up the board."""
    if game.variant_name.lower() == "chess960":
        board = chess.Board(game.initial_fen, chess960=True)
    elif game.variant_name == "From Position":
        board = chess.Board(game.initial_fen)
    else:
        VariantBoard = find_variant(game.variant_name)
        board = VariantBoard()

    for move in game.state["moves"].split():
        try:
            board.push_uci(move)
        except ValueError:
            pass

    return board


def should_exit_game(
    board: chess.Board,
    game: model.Game,
    prior_game,
    li: lichess.Lichess,
) -> bool:
    """Whether we should exit a game."""
    if game.should_abort_now():
        li.abort(game.id)
        return True
    if game.should_terminate_now():
        if game.is_abortable():
            li.abort(game.id)
        return True
    return False


@shared_task(ignore_result=False)
def play_game(
    game_id: str,
) -> None:
    logger = logging.getLogger(__name__)

    response = li.get_game_stream(game_id)
    lines = response.iter_lines()

    # Initial response of stream will be the full game info. Store it.
    initial_state = json.loads(next(lines).decode("utf-8"))
    logger.debug(f"Initial state: {initial_state}")
    abort_time = seconds(CONFIG.abort_time)
    game = model.Game(initial_state, user_profile["username"], li.baseUrl, abort_time)

    engine = MimicTestBot()
    logger.info(f"+++ {game}")

    delay = msec(CONFIG.rate_limiting_delay)

    prior_game = None
    board = chess.Board()
    game_stream = itertools.chain([json.dumps(game.state).encode("utf-8")], lines)
    stay_in_game = True
    while stay_in_game:
        move_attempted = False
        try:
            upd = next_update(game_stream)
            u_type = upd["type"] if upd else "ping"
            if u_type == "gameState":
                game.state = upd
                board = setup_board(game)

                if not is_game_over(game) and is_engine_move(game, prior_game, board):
                    move_attempted = True
                    move = engine.play_move(
                        board,
                        game,
                        li,
                    )
                    li.chat(game_id, "player", json.dumps(move.info))
                    time.sleep(to_seconds(delay))

                prior_game = copy.deepcopy(game)
            elif u_type == "ping" and should_exit_game(board, game, prior_game, li):
                stay_in_game = False
        except (
            HTTPError,
            ReadTimeout,
            RemoteDisconnected,
            ChunkedEncodingError,
            ConnectionError,
            StopIteration,
        ) as e:
            stopped = isinstance(e, StopIteration)
            stay_in_game = not stopped and (
                move_attempted or game_is_active(li, game.id)
            )

    logger.info(f"--- {game.url()} Game over")


def handle_challenge(
    event,
) -> None:
    """Handle incoming challenges. It either accepts, declines, or queues them to accept later."""
    chlng = model.Challenge(event["challenge"], user_profile)
    if chlng.from_self:
        return
    try:
        li.accept_challenge(chlng.id)
    except (HTTPError, ReadTimeout):
        pass


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

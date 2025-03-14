from requests.exceptions import (
    ChunkedEncodingError,
    HTTPError,
    ReadTimeout,
    ConnectionError,
)
from collections import defaultdict
from chess.variant import find_variant
from http.client import RemoteDisconnected
from lib import model, lichess
from lib.timer import to_seconds, seconds, msec
from lib.mimic import MimicTestBot
from flask import g
import time
import chess
import itertools
import copy
import logging
import json


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


def wbtime_param(board: chess.Board):
    """Return `wtime` if it is white's turn to move else `btime`."""
    return "wtime" if board.turn == chess.WHITE else "btime"


def wbinc_param(board: chess.Board):
    """Return `winc` if it is white's turn to move else `binc`."""
    return "winc" if board.turn == chess.WHITE else "binc"


def play_game(game_id: str, li: lichess.Lichess, config, username: str) -> None:
    logger = logging.getLogger(__name__)
    response = li.get_game_stream(game_id)
    lines = response.iter_lines()

    # Initial response of stream will be the full game info. Store it.
    initial_state = json.loads(next(lines).decode("utf-8"))
    logger.debug(f"Initial state: {initial_state}")
    abort_time = seconds(config.abort_time)
    game = model.Game(initial_state, username, li.baseUrl, abort_time)

    logger.info(f"+++ {game}")
    engine = MimicTestBot()

    delay = msec(config.rate_limiting_delay)

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
                # logger.info(f"{game_id}:\n{board}")
                if not is_game_over(game) and is_engine_move(game, prior_game, board):
                    move_attempted = True
                    move = engine.play_move(
                        board,
                        game,
                        li,
                    )
                    li.chat(game_id, "player", json.dumps(move.info))
                    time.sleep(to_seconds(delay))

                wbtime = upd[wbtime_param(board)]
                wbinc = upd[wbinc_param(board)]
                terminate_time = msec(wbtime) + msec(wbinc) + seconds(60)
                game.ping(abort_time, terminate_time, 0)

                prior_game = copy.deepcopy(game)
            elif u_type == "ping" and should_exit_game(board, game, prior_game, li):
                logger.info("should_exit_game returned true")
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
            logger.info(
                f"exception caught: {e}, stopped = {stopped}, stay_in_game = {stay_in_game}"
            )
    engine.reset()
    tell_user_game_result(game, board)
    logger.info(f"--- {game.url()} Game over")


def handle_challenge(event, li: lichess.Lichess, config, user_profile) -> None:
    if len(li.get_ongoing_games()) >= config.concurrency:
        return False, "max_games"

    chlng = model.Challenge(event["challenge"], user_profile)
    if chlng.from_self:
        return True, "self"

    is_supported, decline_reason = chlng.is_supported(
        config, defaultdict(list), defaultdict(lambda: 0)
    )

    if is_supported:
        try:
            li.accept_challenge(chlng.id)
        except (HTTPError, ReadTimeout):
            pass
    else:
        li.decline_challenge(chlng.id, reason=decline_reason)
    return is_supported, decline_reason


def tell_user_game_result(game: model.Game, board: chess.Board) -> None:
    """Log the game result."""

    logger = logging.getLogger(__name__)
    winner = game.state.get("winner")
    termination = game.state.get("status")

    winning_name = game.white.name if winner == "white" else game.black.name
    losing_name = game.white.name if winner == "black" else game.black.name

    if winner is not None:
        logger.info(f"{winning_name} won!")
    elif termination in [model.Termination.DRAW, model.Termination.TIMEOUT]:
        logger.info("Game ended in a draw.")
    else:
        logger.info("Game adjourned.")

    simple_endings: dict[str, str] = {
        model.Termination.MATE: "Game won by checkmate.",
        model.Termination.RESIGN: f"{losing_name} resigned.",
        model.Termination.ABORT: "Game aborted.",
    }

    if termination in simple_endings:
        logger.info(simple_endings[termination])
    elif termination == model.Termination.DRAW:
        draw_results = [
            (board.is_fifty_moves(), "Game drawn by 50-move rule."),
            (board.is_repetition(), "Game drawn by threefold repetition."),
            (
                board.is_insufficient_material(),
                "Game drawn from insufficient material.",
            ),
            (board.is_stalemate(), "Game drawn by stalemate."),
            (True, "Game drawn by agreement."),
        ]
        messages = [
            draw_message for is_result, draw_message in draw_results if is_result
        ]
        logger.info(messages[0])
    elif termination == model.Termination.TIMEOUT:
        if winner:
            logger.info(f"{losing_name} forfeited on time.")
        else:
            timeout_name = (
                game.white.name if game.state.get("wtime") == 0 else game.black.name
            )
            other_name = (
                game.white.name if timeout_name == game.black.name else game.black.name
            )
            logger.info(
                f"{timeout_name} ran out of time, but {other_name} did not have enough material to mate."
            )
    elif termination:
        logger.info(f"Game ended by {termination}")

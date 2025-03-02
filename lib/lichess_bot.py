"""The main module that controls lichess-bot."""
import argparse
import copy
import datetime
import http.server
import itertools
import json
import logging
import logging.handlers
import math
import multiprocessing
import os
import signal
import socketserver
import time
from collections import Counter, defaultdict
from collections.abc import Iterator, MutableSequence
from functools import partial
from http.client import RemoteDisconnected
from multiprocessing.pool import Pool
from queue import Empty
from types import FrameType
from typing import Optional, TypedDict, cast

import backoff
import chess
import yaml
from chess.variant import find_variant
from requests.exceptions import (
    ChunkedEncodingError,
    HTTPError,
    ReadTimeout,
)
from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
)
from rich.logging import RichHandler

from lib.mimic import MimicTestBot
from lib import lichess, matchmaking, model
from lib.config import Configuration, load_config, log_config
from lib.lichess_types import (
    CONTROL_QUEUE_TYPE,
    LOGGING_QUEUE_TYPE,
    EventType,
    GameEventType,
    GameType,
    UserProfileType,
)
from lib.timer import Timer, hours, msec, seconds, to_seconds

MULTIPROCESSING_LIST_TYPE = MutableSequence[model.Challenge]
POOL_TYPE = Pool


class VersioningType(TypedDict):
    """Type hint for the versioning information from lib/versioning.yml."""

    lichess_bot_version: str
    minimum_python_version: str
    deprecated_python_version: str
    deprecation_date: datetime.date


logger = logging.getLogger(__name__)

with open("lib/versioning.yml") as version_file:
    versioning_info: VersioningType = yaml.safe_load(version_file)

__version__ = versioning_info["lichess_bot_version"]


class ServerHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, control_queue, *args, **kwargs):
        self.control_queue = control_queue
        super().__init__(*args, **kwargs)

    def do_POST(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        content_length = int(
            self.headers["Content-Length"]
        )  # <--- Gets the size of data
        post_data = self.rfile.read(content_length)  # <--- Gets the data itself
        event = json.loads(post_data.decode("utf-8"))
        self.control_queue.put_nowait(event)

    def log_message(self, format, *args):
        return


class PlayGameArgsType(TypedDict, total=False):
    """Type hint for `play_game_args`."""

    li: lichess.Lichess
    control_queue: CONTROL_QUEUE_TYPE
    user_profile: UserProfileType
    config: Configuration
    challenge_queue: MULTIPROCESSING_LIST_TYPE
    logging_queue: LOGGING_QUEUE_TYPE
    game_id: str


terminated = False
force_quit = False
restart = True


def should_restart() -> bool:
    """Decide whether to restart lichess-bot when exiting main program."""
    return restart


def disable_restart() -> None:
    """Disable restarting lichess-bot when errors occur. Used during testing."""
    global restart
    restart = False


def signal_handler(signal: int, frame: Optional[FrameType]) -> None:  # noqa: ARG001
    """Terminate lichess-bot."""
    global terminated
    global force_quit
    in_starting_thread = __name__ == "__main__"
    if not terminated:
        if in_starting_thread:
            logger.debug("Received SIGINT. Terminating client.")
        terminated = True
    else:
        if in_starting_thread:
            logger.debug("Received second SIGINT. Quitting now.")
        force_quit = True


signal.signal(signal.SIGINT, signal_handler)


def watch_control_stream(
    control_queue: CONTROL_QUEUE_TYPE, li: lichess.Lichess
) -> None:
    handler = partial(ServerHandler, control_queue)
    with socketserver.TCPServer(("localhost", 5001), handler) as server:
        server.serve_forever()

    control_queue.put_nowait({"type": "terminated", "error": None})


def logging_configurer(
    level: int, filename: Optional[str]) -> None:
    """
    Configure the logger.

    :param level: The logging level. Either `logging.INFO` or `logging.DEBUG`.
    :param filename: The filename to write the logs to. If it is `None` then the logs aren't written to a file.
    """
    console_handler = RichHandler()
    console_formatter = logging.Formatter("%(message)s")
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(level)
    all_handlers: list[logging.Handler] = [console_handler]

    if filename:
        file_handler = logging.FileHandler(filename, delay=True, encoding="utf-8")
        FORMAT = (
            "%(asctime)s %(name)s (%(filename)s:%(lineno)d) %(levelname)s %(message)s"
        )
        file_formatter = logging.Formatter(FORMAT)
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(level)
        all_handlers.append(file_handler)
        
    logging.basicConfig(level=logging.DEBUG, handlers=all_handlers, force=True)


def logging_listener_proc(
    queue: LOGGING_QUEUE_TYPE,
    level: int,
    log_filename: Optional[str],
) -> None:
    """
    Handle events from the logging queue.

    This allows the logs from inside a thread to be printed.
    They are added to the queue, so they are printed outside the thread.
    """
    logging_configurer(level, log_filename)
    logger = logging.getLogger()
    while True:
        task: Optional[logging.LogRecord] = None
        try:
            task = queue.get(block=False)
        except Empty:
            time.sleep(0.1)
        except InterruptedError:
            pass
        except Exception:  # noqa: S110
            pass

        if task is None:
            continue

        logger.handle(task)
        queue.task_done()


def thread_logging_configurer(queue: LOGGING_QUEUE_TYPE) -> None:
    """Configure the game logger."""
    h = logging.handlers.QueueHandler(queue)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(h)
    root.setLevel(logging.DEBUG)


def start(
    li: lichess.Lichess,
    user_profile: UserProfileType,
    config: Configuration,
    logging_level: int,
    log_filename: Optional[str],
    one_game: bool = False,
) -> None:
    """
    Start lichess-bot.

    :param li: Provides communication with lichess.org.
    :param user_profile: Information on our bot.
    :param config: The config that the bot will use.
    :param logging_level: The logging level. Either `logging.INFO` or `logging.DEBUG`.
    :param log_filename: The filename to write the logs to. If it is `None` then the logs aren't written to a file.
    :param one_game: Whether the bot should play only one game. Only used in `test_bot/test_bot.py` to test lichess-bot.
    """
    logger.info(f"You're now connected to {config.url} and awaiting challenges.")
    manager = multiprocessing.Manager()
    challenge_queue: MULTIPROCESSING_LIST_TYPE = manager.list()
    control_queue: CONTROL_QUEUE_TYPE = manager.Queue()
    control_stream = multiprocessing.Process(
        target=watch_control_stream, args=(control_queue, li)
    )
    control_stream.start()

    logging_queue = manager.Queue()
    logging_listener = multiprocessing.Process(
        target=logging_listener_proc,
        args=(logging_queue, logging_level, log_filename),
    )
    logging_listener.start()

    thread_logging_configurer(logging_queue)

    try:
        lichess_bot_main(
            li,
            user_profile,
            config,
            challenge_queue,
            control_queue,
            logging_queue,
            one_game,
        )
    finally:
        control_stream.terminate()
        control_stream.join()
        time.sleep(1.0)  # Allow final messages in logging_queue to be handled.
        logging_configurer(logging_level, log_filename)
        logging_listener.terminate()
        logging_listener.join()


def log_proc_count(change: str, active_games: set[str]) -> None:
    """
    Log the number of active games and their IDs.

    :param change: Either "Freed", "Used", or "Queued".
    :param active_games: A set containing the IDs of the active games.
    """
    symbol = "+++" if change == "Freed" else "---"
    logger.info(
        f"{symbol} Process {change}. Count: {len(active_games)}. IDs: {active_games or None}"
    )


def lichess_bot_main(
    li: lichess.Lichess,
    user_profile: UserProfileType,
    config: Configuration,
    challenge_queue: MULTIPROCESSING_LIST_TYPE,
    control_queue: CONTROL_QUEUE_TYPE,
    logging_queue: LOGGING_QUEUE_TYPE,
    one_game: bool,
) -> None:
    """
    Handle all the games and challenges.

    :param li: Provides communication with lichess.org.
    :param user_profile: Information on our bot.
    :param config: The config that the bot will use.
    :param challenge_queue: The queue containing the challenges.
    :param control_queue: The queue containing all the events.
    :param correspondence_queue: The queue containing the correspondence games.
    :param logging_queue: The logging queue. Used by `logging_listener_proc`.
    :param one_game: Whether the bot should play only one game. Only used in `test_bot/test_bot.py` to test lichess-bot.
    """
    global restart

    max_games = config.challenge.concurrency

    one_game_completed = False

    all_games = li.get_ongoing_games()
    active_games = {game["gameId"] for game in all_games}
    low_time_games: list[GameType] = []

    last_check_online_time = Timer(hours(1))
    matchmaker = matchmaking.Matchmaking(li, config, user_profile)
    matchmaker.show_earliest_challenge_time()

    play_game_args = PlayGameArgsType(
        li=li,
        control_queue=control_queue,
        user_profile=user_profile,
        config=config,
        challenge_queue=challenge_queue,
        logging_queue=logging_queue,
    )

    recent_bot_challenges: defaultdict[str, list[Timer]] = defaultdict(list)

    if config.quit_after_all_games_finish:
        logger.info(
            "When quitting, lichess-bot will first wait for all running games to finish."
        )
        logger.info("Press Ctrl-C twice to quit immediately.")

    with multiprocessing.pool.Pool(max_games + 1) as pool:
        while not (terminated or (one_game and one_game_completed) or restart):
            event = next_event(control_queue)
            if not event:
                continue

            if event["type"] == "terminated":
                restart = True
                logger.debug(f"Terminating exception:\n{event['error']}")
                control_queue.task_done()
                break

            if event["type"] == "local_game_done":
                active_games.discard(event["game"]["id"])
                matchmaker.game_done()
                log_proc_count("Freed", active_games)
                one_game_completed = True
            elif event["type"] == "challenge":
                handle_challenge(
                    event,
                    li,
                    challenge_queue,
                    config.challenge,
                    user_profile,
                    recent_bot_challenges,
                )
            elif event["type"] == "challengeDeclined":
                matchmaker.declined_challenge(event)
            elif event["type"] == "gameStart":
                matchmaker.accepted_challenge(event)
                start_game(
                    event,
                    pool,
                    play_game_args,
                    config,
                    active_games,
                    low_time_games,
                )

            start_low_time_games(
                low_time_games, active_games, max_games, pool, play_game_args
            )
            accept_challenges(li, challenge_queue, active_games, max_games)
            matchmaker.challenge(active_games, challenge_queue, max_games)
            check_online_status(li, user_profile, last_check_online_time)

            control_queue.task_done()

        close_pool(pool, active_games, config)


def close_pool(pool: POOL_TYPE, active_games: set[str], config: Configuration) -> None:
    """Shut down pool after possibly waiting on games to finish depending on the configuration."""
    if config.quit_after_all_games_finish:
        if active_games:
            logger.info("Waiting for games to finish before quitting.")
        pool.close()
        pool.join()


def next_event(control_queue: CONTROL_QUEUE_TYPE) -> EventType:
    """Get the next event from the control queue."""
    try:
        event = control_queue.get()
        if event is None:
            return {}
    except InterruptedError:
        return {}

    if "type" not in event:
        logger.warning("Unable to handle response from lichess.org:")
        logger.warning(event)
        control_queue.task_done()
        return {}

    if event.get("type") != "ping":
        logger.debug(f"Event: {event}")

    return event


def start_low_time_games(
    low_time_games: list[GameType],
    active_games: set[str],
    max_games: int,
    pool: POOL_TYPE,
    play_game_args: PlayGameArgsType,
) -> None:
    """Start the games based on how much time we have left."""
    low_time_games.sort(key=lambda g: g.get("secondsLeft", math.inf))
    while low_time_games and len(active_games) < max_games:
        game_id = low_time_games.pop(0)["id"]
        start_game_thread(active_games, game_id, play_game_args, pool)


def accept_challenges(
    li: lichess.Lichess,
    challenge_queue: MULTIPROCESSING_LIST_TYPE,
    active_games: set[str],
    max_games: int,
) -> None:
    """Accept a challenge."""
    while len(active_games) < max_games and challenge_queue:
        chlng = challenge_queue.pop(0)
        if chlng.from_self:
            continue

        try:
            logger.info(f"Accept {chlng}")
            li.accept_challenge(chlng.id)
            active_games.add(chlng.id)
            log_proc_count("Queued", active_games)
        except (HTTPError, ReadTimeout) as exception:
            if (
                isinstance(exception, HTTPError)
                and exception.response is not None
                and exception.response.status_code == 404
            ):
                logger.info(f"Skip missing {chlng}")


def check_online_status(
    li: lichess.Lichess, user_profile: UserProfileType, last_check_online_time: Timer
) -> None:
    """Check if lichess.org thinks the bot is online or not. If it isn't, we restart it."""
    global restart

    if last_check_online_time.is_expired():
        try:
            if not li.is_online(user_profile["id"]):
                logger.info("Will restart lichess-bot")
                restart = True
            last_check_online_time.reset()
        except (HTTPError, ReadTimeout):
            pass


def sort_challenges(
    challenge_queue: MULTIPROCESSING_LIST_TYPE, challenge_config: Configuration
) -> None:
    """
    Sort the challenges.

    They can be sorted either by rating (the best challenger is accepted first),
    or by time (the first challenger is accepted first). The bot can also
    prioritize playing against humans or bots.
    """
    challenge_list = list(challenge_queue)
    if challenge_config.sort_by == "best":
        challenge_list.sort(key=lambda challenger: challenger.score(), reverse=True)
    if challenge_config.preference != "none":
        challenge_list.sort(
            key=lambda challenger: challenger.challenger.is_bot,
            reverse=challenge_config.preference == "bot",
        )
    challenge_queue[:] = challenge_list


def game_is_active(li: lichess.Lichess, game_id: str) -> bool:
    """Determine if a game is still being played."""
    return game_id in (
        ongoing_game["gameId"] for ongoing_game in li.get_ongoing_games()
    )


def start_game_thread(
    active_games: set[str],
    game_id: str,
    play_game_args: PlayGameArgsType,
    pool: POOL_TYPE,
) -> None:
    """Start a game thread."""
    active_games.add(game_id)
    log_proc_count("Used", active_games)
    play_game_args["game_id"] = game_id

    def game_error_handler(error: BaseException) -> None:
        logger.exception("Game ended due to error:", exc_info=error)
        control_queue = play_game_args["control_queue"]
        control_queue.put_nowait({"type": "local_game_done", "game": {"id": game_id}})

    pool.apply_async(play_game, kwds=play_game_args, error_callback=game_error_handler)


def start_game(
    event: EventType,
    pool: POOL_TYPE,
    play_game_args: PlayGameArgsType,
    config: Configuration,
    active_games: set[str],
    low_time_games: list[GameType],
) -> None:
    """
    Start a game.

    :param event: The gameStart event.
    :param pool: The thread pool that the game is added to, so they can be run asynchronously.
    :param play_game_args: The args passed to `play_game`.
    :param config: The config the bot will use.
    :param active_games: A set of all the games that aren't correspondence games.
    :param low_time_games: A list of games, in which we don't have much time remaining.
    """
    game_id = event["game"]["id"]
    start_game_thread(active_games, game_id, play_game_args, pool)


def handle_challenge(
    event: EventType,
    li: lichess.Lichess,
    challenge_queue: MULTIPROCESSING_LIST_TYPE,
    challenge_config: Configuration,
    user_profile: UserProfileType,
    recent_bot_challenges: defaultdict[str, list[Timer]],
) -> None:
    """Handle incoming challenges. It either accepts, declines, or queues them to accept later."""
    chlng = model.Challenge(event["challenge"], user_profile)
    if chlng.from_self:
        return

    players_with_active_games = Counter(
        game["opponent"]["username"] for game in li.get_ongoing_games()
    )

    is_supported, decline_reason = chlng.is_supported(
        challenge_config, recent_bot_challenges, players_with_active_games
    )
    if is_supported:
        challenge_queue.append(chlng)
        sort_challenges(challenge_queue, challenge_config)
        time_window = challenge_config.recent_bot_challenge_age
        if time_window is not None:
            recent_bot_challenges[chlng.challenger.name].append(
                Timer(seconds(time_window))
            )
    else:
        li.decline_challenge(chlng.id, reason=decline_reason)


@backoff.on_exception(
    backoff.expo,
    BaseException,
    max_time=600,
    giveup=lichess.is_final,  # type: ignore[arg-type]
    on_backoff=lichess.backoff_handler,
)
def play_game(
    li: lichess.Lichess,
    game_id: str,
    control_queue: CONTROL_QUEUE_TYPE,
    user_profile: UserProfileType,
    config: Configuration,
    challenge_queue: MULTIPROCESSING_LIST_TYPE,
    logging_queue: LOGGING_QUEUE_TYPE,
) -> None:
    """
    Play a game.

    :param li: Provides communication with lichess.org.
    :param game_id: The id of the game.
    :param control_queue: The control queue that contains events (adds `local_game_done` to the queue).
    :param user_profile: Information on our bot.
    :param config: The config that the bot will use.
    :param challenge_queue: The queue containing the challenges.
    :param logging_queue: The logging queue. Used by `logging_listener_proc`.
    """
    logger = logging.getLogger(__name__)

    response = li.get_game_stream(game_id)
    lines = response.iter_lines()

    # Initial response of stream will be the full game info. Store it.
    initial_state = json.loads(next(lines).decode("utf-8"))
    logger.debug(f"Initial state: {initial_state}")
    abort_time = seconds(config.abort_time)
    game = model.Game(initial_state, user_profile["username"], li.baseUrl, abort_time)

    engine = MimicTestBot()
    logger.info(f"+++ {game}")

    delay = msec(config.rate_limiting_delay)

    disconnect_time = seconds(0)
    prior_game = None
    board = chess.Board()
    game_stream = itertools.chain([json.dumps(game.state).encode("utf-8")], lines)
    quit_after_all_games_finish = config.quit_after_all_games_finish
    stay_in_game = True
    while (
        stay_in_game
        and (not terminated or quit_after_all_games_finish)
        and not force_quit
    ):
        move_attempted = False
        try:
            upd = next_update(game_stream)
            u_type = upd["type"] if upd else "ping"
            if u_type == "gameState":
                game.state = upd
                board = setup_board(game)

                if not is_game_over(game) and is_engine_move(
                    game, prior_game, board
                ):
                    print_move_number(board)
                    move_attempted = True
                    move = engine.play_move(
                        board,
                        game,
                        li,
                    )
                    send_elo_predictions(li, game_id, move.info)
                    time.sleep(to_seconds(delay))
                elif is_game_over(game):
                    tell_user_game_result(game, board)

                prior_game = copy.deepcopy(game)
            elif u_type == "ping" and should_exit_game(
                board, game, prior_game, li, False
            ):
                stay_in_game = False
        except (
            HTTPError,
            ReadTimeout,
            RemoteDisconnected,
            ChunkedEncodingError,
            RequestsConnectionError,
            StopIteration,
        ) as e:
            stopped = isinstance(e, StopIteration)
            stay_in_game = not stopped and (
                move_attempted or game_is_active(li, game.id)
            )

    logger.info(f"--- {game.url()} Game over")
    control_queue.put_nowait({"type": "local_game_done", "game": {"id": game.id}})


def send_elo_predictions(li: lichess.Lichess, game_id: str, info: dict) -> None:
    li.chat(game_id, "player", json.dumps(info))


def print_move_number(board: chess.Board) -> None:
    """Log the move number."""
    logger.info("")
    logger.info(f"move: {len(board.move_stack) // 2 + 1}")


def next_update(lines: Iterator[bytes]) -> GameEventType:
    """Get the next game state."""
    binary_chunk = next(lines)
    upd = (
        cast(GameEventType, json.loads(binary_chunk.decode("utf-8")))
        if binary_chunk
        else {}
    )
    if upd:
        logger.debug(f"Game state: {upd}")
    return upd


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
            logger.exception(f"Ignoring illegal move {move} on board {board.fen()}")

    return board


def is_engine_move(
    game: model.Game, prior_game: Optional[model.Game], board: chess.Board
) -> bool:
    """Check whether it is the engine's turn."""
    return game_changed(game, prior_game) and bot_to_move(game, board)


def bot_to_move(game: model.Game, board: chess.Board) -> bool:
    """
    Determine whether it is the bot's move on the given board.

    This only determines if the board state shows the bot is on move. It does not check if the board state has changed.
    Messages from lichess can contain repeat board states if another game aspect has changed (draw offer, takeback offer,
    etc.). Use is_engine_move() to determine if the engine should play a move.
    """
    return game.is_white == (board.turn == chess.WHITE)


def is_game_over(game: model.Game) -> bool:
    """Check whether the game is over."""
    status = game.state["status"]
    return status != "started"


def should_exit_game(
    board: chess.Board,
    game: model.Game,
    prior_game: Optional[model.Game],
    li: lichess.Lichess,
    is_correspondence: bool,
) -> bool:
    """Whether we should exit a game."""
    if (
        is_correspondence
        and not is_engine_move(game, prior_game, board)
        and game.should_disconnect_now()
    ):
        return True
    if game.should_abort_now():
        logger.info(f"Aborting {game.url()} by lack of activity")
        li.abort(game.id)
        return True
    if game.should_terminate_now():
        logger.info(f"Terminating {game.url()} by lack of activity")
        if game.is_abortable():
            li.abort(game.id)
        return True
    return False


def game_changed(current_game: model.Game, prior_game: Optional[model.Game]) -> bool:
    """Check whether the current game state is different from the previous game state."""
    if prior_game is None:
        return True

    current_game_moves_str = current_game.state["moves"]
    prior_game_moves_str = prior_game.state["moves"]
    return current_game_moves_str != prior_game_moves_str


def tell_user_game_result(game: model.Game, board: chess.Board) -> None:
    """Log the game result."""
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


def start_lichess_bot() -> None:
    """Parse arguments passed to lichess-bot.py and starts lichess-bot."""
    parser = argparse.ArgumentParser(description="Play on Lichess with a bot")
    parser.add_argument(
        "-v",
        action="store_true",
        help="Make output more verbose. Include all communication with lichess.",
    )
    parser.add_argument(
        "-l", "--logfile", help="Record all console output to a log file.", default=None
    )
    args = parser.parse_args()

    logging_level = logging.DEBUG if args.v else logging.INFO
    logging_configurer(logging_level, args.logfile)

    CONFIG = load_config("./config.yml")
    max_retries = CONFIG.engine.online_moves.max_retries
    li = lichess.Lichess(
        CONFIG.token, CONFIG.url, __version__, logging_level, max_retries
    )

    user_profile = li.get_profile()

    start(
        li,
        user_profile,
        CONFIG,
        logging_level,
        args.logfile,
    )
    logging.shutdown()


def start_program() -> None:
    """Start lichess-bot and restart when needed."""
    multiprocessing.set_start_method("spawn")

    try:
        while should_restart():
            disable_restart()
            start_lichess_bot()
            time.sleep(10 if should_restart() else 0)
    except Exception:
        logger.exception("Quitting lichess-bot due to an error:")

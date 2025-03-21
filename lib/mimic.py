import os
import pathlib

import chess
from chess.engine import PlayResult
import numpy as np
import torch
from xata.client import XataClient

from lib.dual_zero_v04.config import get_config
from lib.dual_zero_v04.model import ModelArgs, Transformer
from lib.pgnUtils import STARTMV, BoardState, IllegalMoveException
from lib import model, lichess

xata = XataClient()


class MimicTestBot:
    def __init__(self):
        self.core = MimicTestBotCore()
        self.games = {}

    def default_elo(self):
        return self.core.default_elo

    def add_game(self, gameId):
        m, s = self.core.whiten_params
        self.games[gameId] = {
            "board": BoardState(),
            "inp": torch.tensor([[STARTMV]], dtype=torch.int32),
            "welo": f"{int(m)},{int(s**2)}",
            "belo": f"{int(m)},{int(s**2)}",
        }

    def remove_game(self, gameId):
        del self.games[gameId]

    def play_move(
        self,
        board: chess.Board,
        game: model.Game,
        li: lichess.Lichess,
    ) -> None:
        best_move = self.search(board, game.id)
        li.make_move(game.id, best_move)
        return best_move

    def _update_elos(self, gameId, elo_preds):
        def update_elo(name):
            ep = elo_preds[name + "Params"]
            self.games[gameId][name] = (
                f"{self.games[gameId][name]},{int(ep['m'])},{int(ep['s'])}"
            )

        print(self.games[gameId]["welo"])
        for name in ["welo", "belo"]:
            update_elo(name)
            xata.records().update("game", gameId, {name: self.games[gameId][name]})

    def search(self, board: chess.Board, gameId: str) -> PlayResult:
        last = None
        if len(board.move_stack) > 0:
            last = board.peek().uci()

        core_state = self.games[gameId]["board"]
        inp = self.games[gameId]["inp"]
        mv, elo_preds, inp = self.core.predict(last, core_state, inp)
        self.games[gameId]["inp"] = inp
        self._update_elos(gameId, elo_preds)
        return PlayResult(mv, None, info=elo_preds)


class Wrapper(torch.nn.Module):
    def __init__(self, ptmodel):
        super().__init__()
        self.model = ptmodel

    def forward(self, inp):
        return self.model(inp)


def get_model_args(cfgyml):
    model_args = ModelArgs(cfgyml.model_args.__dict__)
    if cfgyml.elo_params.predict:
        model_args.gaussian_elo = cfgyml.elo_params.loss == "gaussian_nll"
        if cfgyml.elo_params.loss == "cross_entropy":
            model_args.elo_pred_size = len(cfgyml.elo_params.edges) + 1
        elif cfgyml.elo_params.loss == "gaussian_nll":
            model_args.elo_pred_size = 2
        elif cfgyml.elo_params.loss == "mse":
            model_args.elo_pred_size = 1
        else:
            raise Exception("did not recognize loss function name")
    model_args.n_timecontrol_heads = len([
        n for _, grp in cfgyml.tc_groups.items() for n in grp
    ])
    return model_args


class MimicTestBotCore:
    def __init__(self, top_n=10, p_thresh=0.2):
        dn = pathlib.Path(__file__).parent.resolve()
        cfg = os.path.join(dn, "dual_zero_v04", "cfg.yml")
        cfgyml = get_config(cfg)
        self.tc_groups = cfgyml.tc_groups
        self.whiten_params = cfgyml.elo_params.whiten_params
        model_args = get_model_args(cfgyml)
        self.model = Wrapper(Transformer(model_args))
        cp = torch.load(
            os.path.join(dn, "dual_zero_v04", "weights.ckpt"),
            map_location=torch.device("cpu"),
            weights_only=True,
        )
        self.model.load_state_dict(cp)
        self.model.eval()

        self.top_n = top_n
        self.p_thresh = p_thresh

        wm, ws = self.whiten_params
        def_elo = {"m": wm, "s": ws**2}
        self.default_elo = {"weloParams": def_elo, "beloParams": def_elo}

    def _add_move(self, mvid, inp):
        mv = torch.tensor([[mvid]], dtype=torch.int32)
        return torch.cat([inp, mv], dim=1)

    def _create_elo_info(self, elo_pred):
        wm, ws = self.whiten_params
        ms = elo_pred[0, :, 0, 0] * ws + wm
        ss = ((elo_pred[0, :, 0, 1] ** 0.5) * ws) ** 2

        if len(ms) % 2 == 0:
            widx = -2
            bidx = -1
        else:
            widx = -1
            bidx = -2

        return {
            "weloParams": {"m": ms[widx].item(), "s": ss[widx].item()},
            "beloParams": {"m": ms[bidx].item(), "s": ss[bidx].item()},
        }

    @torch.inference_mode
    def predict(self, uci: str, state: BoardState, inp: torch.Tensor) -> chess.Move:
        if uci is not None:
            mvid = state.uci_to_mvid(uci)
            state.update(mvid)
            inp = self._add_move(mvid, inp)

        mv_pred, elo_pred = self.model(inp)

        if uci is not None:
            info = self._create_elo_info(elo_pred)
        else:
            info = self.default_elo

        probs, mvids = mv_pred[0, -1, -1, -1].softmax(dim=0).sort(descending=True)
        p = probs[: self.top_n].double() / probs[: self.top_n].double().sum()
        p[p < self.p_thresh] = 1e-8
        p = p / p.sum()
        mvids = np.random.choice(
            mvids[: self.top_n], size=self.top_n, replace=False, p=p
        )
        for mvid in mvids:
            try:
                mv = state.update(mvid)
                inp = self._add_move(mvid, inp)
                break
            except IllegalMoveException:
                continue
        else:
            raise Exception("model did not produce a legal move")
        return mv, info, inp

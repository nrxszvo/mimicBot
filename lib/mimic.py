import os
import pathlib

import chess
from chess.engine import PlayResult
import numpy as np
import torch

from lib.dual_zero_v04.config import get_config
from lib.dual_zero_v04.model import ModelArgs, Transformer
from lib.pgnUtils import STARTMV, BoardState, IllegalMoveException
from lib import model, lichess

class MimicTestBot:
    def __init__(self):
        self.core = MimicTestBotCore()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get_pid(self):
        return "?"

    def play_move(
        self,
        board: chess.Board,
        game: model.Game,
        li: lichess.Lichess,
    ) -> None:
        """
        Play a move.

        :param board: The current position.
        :param game: The game that the bot is playing.
        :param li: Provides communication with lichess.org.
        :return: The move to play.
        """
        best_move = self.search(board)
        li.make_move(game.id, best_move)
        return best_move

    def search(self, board: chess.Board) -> PlayResult:
        last = None
        if len(board.move_stack) > 0:
            last = board.peek().uci()
        mv, elo_preds = self.core.predict(last)
        return PlayResult(mv, None, info=elo_preds)



class Wrapper(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model

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
    model_args.n_timecontrol_heads = len(
        [n for _, grp in cfgyml.tc_groups.items() for n in grp]
    )
    return model_args


class MimicTestBotCore:
    def __init__(self):
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
        self.board = BoardState()
        self.inp = torch.tensor([[STARTMV]], dtype=torch.int32)
        self.ms = []

    def _add_move(self, mvid):
        mv = torch.tensor([[mvid]], dtype=torch.int32)
        self.inp = torch.cat([self.inp, mv], dim=1)

    @torch.inference_mode
    def predict(self, uci: str) -> chess.Move:
        wm, ws = self.whiten_params

        def parse_elo(elo_pred):
            m = elo_pred[0, :, 0, 0] * ws + wm
            s = ((elo_pred[0, :, 0, 1] ** 0.5) * ws) ** 2
            return m, s

        if uci is not None:
            mvid = self.board.uci_to_mvid(uci)
            self.board.update(mvid)
            self._add_move(mvid)

        mv_pred, elo_pred = self.model(self.inp)

        def_elo = {"m": wm, "s": ws**2}
        info = {"weloParams": def_elo, "beloParams": def_elo}
        if uci is not None:
            ms, ss = parse_elo(elo_pred)
            if len(ms) % 2 == 0:
                widx = -2
                bidx = -1
            else:
                widx = -1
                bidx = -2
            info = {
                "weloParams": {"m": ms[widx].item(), "s": ss[widx].item()},
                "beloParams": {"m": ms[bidx].item(), "s": ss[bidx].item()},
            }
        probs, mvids = mv_pred[0, -1, -1, -1].softmax(dim=0).sort(descending=True)
        p = probs[:5].double() / probs[:5].double().sum()
        p[p < 0.05] = 1e-8
        p = p / p.sum()
        mvids = np.random.choice(mvids[:5], size=5, replace=False, p=p)
        for mvid in mvids:
            try:
                mv = self.board.update(mvid)
                self._add_move(mvid)
                break
            except IllegalMoveException:
                continue
        else:
            raise Exception("model did not produce a legal move")
        return mv, info

# Copyright (c) Meta Platforms, Inc. and affiliates.
# This software may be used and distributed in accordance with the terms of the Llama 3 Community License Agreement.

import math
from dataclasses import dataclass
from typing import Optional, Tuple

import torch
import torch.nn.functional as F
from torch import nn


@dataclass
class ModelArgs:
    dim: int = 4096
    n_layers: int = 32
    n_heads: int = 32
    n_kv_heads: Optional[int] = None
    vocab_size: int = 2048
    predict_move: bool = True
    elo_pred_size: int = 10
    gaussian_elo: bool = False
    n_elo_heads: int = 10
    n_timecontrol_heads: int = 1
    multiple_of: int = 256  # make SwiGLU hidden layer size multiple of large power of 2
    ffn_dim_multiplier: Optional[float] = None
    norm_eps: float = 1e-5
    rope_theta: float = 500000
    max_seq_len: int = 512

    def __init__(self, paramd):
        self.__dict__ = paramd


class RMSNorm(torch.nn.Module):
    def __init__(self, dim: int, eps: float = 1e-6):
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(dim))

    def _norm(self, x):
        return x * torch.rsqrt(x.pow(2).mean(-1, keepdim=True) + self.eps)

    def forward(self, x):
        output = self._norm(x.float()).type_as(x)
        return output * self.weight


def precompute_freqs_cis(dim: int, end: int, theta: float = 10000.0):
    freqs = 1.0 / (theta ** (torch.arange(0, dim, 2)
                   [: (dim // 2)].float() / dim))
    t = torch.arange(end, device=freqs.device, dtype=torch.float32)
    freqs = torch.outer(t, freqs)
    freqs_cis = torch.polar(torch.ones_like(freqs), freqs)  # complex64
    return freqs_cis


def reshape_for_broadcast(freqs_cis: torch.Tensor, x: torch.Tensor):
    ndim = x.ndim
    assert 0 <= 1 < ndim
    assert freqs_cis.shape == (x.shape[1], x.shape[-1])
    shape = [d if i == 1 or i == ndim -
             1 else 1 for i, d in enumerate(x.shape)]
    return freqs_cis.view(*shape)


def apply_rotary_emb(
    xq: torch.Tensor,
    xk: torch.Tensor,
    freqs_cis: torch.Tensor,
) -> Tuple[torch.Tensor, torch.Tensor]:
    xq_ = torch.view_as_complex(xq.float().reshape(*xq.shape[:-1], -1, 2))
    xk_ = torch.view_as_complex(xk.float().reshape(*xk.shape[:-1], -1, 2))
    freqs_cis = reshape_for_broadcast(freqs_cis, xq_)
    xq_out = torch.view_as_real(xq_ * freqs_cis).flatten(3)
    xk_out = torch.view_as_real(xk_ * freqs_cis).flatten(3)
    return xq_out.type_as(xq), xk_out.type_as(xk)


def repeat_kv(x: torch.Tensor, n_rep: int) -> torch.Tensor:
    """torch.repeat_interleave(x, dim=2, repeats=n_rep)"""
    bs, slen, n_kv_heads, head_dim = x.shape
    if n_rep == 1:
        return x
    return (
        x[:, :, :, None, :]
        .expand(bs, slen, n_kv_heads, n_rep, head_dim)
        .reshape(bs, slen, n_kv_heads * n_rep, head_dim)
    )


class Attention(nn.Module):
    def __init__(self, args: ModelArgs):
        super().__init__()
        self.n_kv_heads = args.n_heads if args.n_kv_heads is None else args.n_kv_heads
        self.n_local_heads = args.n_heads
        self.n_local_kv_heads = self.n_kv_heads
        self.head_dim = args.dim // args.n_heads

        self.n_rep = self.n_local_heads // self.n_local_kv_heads

        self.wq = nn.Linear(
            args.dim,
            args.n_heads * self.head_dim,
            bias=False,
        )
        self.wk = nn.Linear(
            args.dim,
            self.n_kv_heads * self.head_dim,
            bias=False,
        )
        self.wv = nn.Linear(
            args.dim,
            self.n_kv_heads * self.head_dim,
            bias=False,
        )
        self.wo = nn.Linear(
            args.n_heads * self.head_dim,
            args.dim,
            bias=False,
        )

    def forward(
        self,
        x: torch.Tensor,
        start_pos: int,
        freqs_cis: torch.Tensor,
        mask: Optional[torch.Tensor],
    ):
        bsz, seqlen, _ = x.shape
        xq, xk, xv = self.wq(x), self.wk(x), self.wv(x)

        xq = xq.view(bsz, seqlen, self.n_local_heads, self.head_dim)
        xk = xk.view(bsz, seqlen, self.n_local_kv_heads, self.head_dim)
        xv = xv.view(bsz, seqlen, self.n_local_kv_heads, self.head_dim)

        xq, xk = apply_rotary_emb(xq, xk, freqs_cis=freqs_cis)

        keys = xk
        values = xv

        # repeat k/v heads if n_kv_heads < n_heads
        keys = repeat_kv(
            keys, self.n_rep
        )  # (bs, cache_len + seqlen, n_local_heads, head_dim)
        values = repeat_kv(
            values, self.n_rep
        )  # (bs, cache_len + seqlen, n_local_heads, head_dim)

        xq = xq.transpose(1, 2)  # (bs, n_local_heads, seqlen, head_dim)
        # (bs, n_local_heads, cache_len + seqlen, head_dim)
        keys = keys.transpose(1, 2)
        values = values.transpose(
            1, 2
        )  # (bs, n_local_heads, cache_len + seqlen, head_dim)
        scores = torch.matmul(xq, keys.transpose(2, 3)) / \
            math.sqrt(self.head_dim)
        if mask is not None:
            # (bs, n_local_heads, seqlen, cache_len + seqlen)
            scores = scores + mask
        scores = F.softmax(scores.float(), dim=-1).type_as(xq)
        # (bs, n_local_heads, seqlen, head_dim)
        output = torch.matmul(scores, values)
        output = output.transpose(1, 2).contiguous().view(bsz, seqlen, -1)
        return self.wo(output)


class FeedForward(nn.Module):
    def __init__(
        self,
        dim: int,
        hidden_dim: int,
        multiple_of: int,
        ffn_dim_multiplier: Optional[float],
    ):
        super().__init__()
        hidden_dim = int(2 * hidden_dim / 3)
        # custom dim factor multiplier
        if ffn_dim_multiplier is not None:
            hidden_dim = int(ffn_dim_multiplier * hidden_dim)
        hidden_dim = multiple_of * \
            ((hidden_dim + multiple_of - 1) // multiple_of)

        self.w1 = nn.Linear(dim, hidden_dim, bias=False)
        self.w2 = nn.Linear(hidden_dim, dim, bias=False)
        self.w3 = nn.Linear(dim, hidden_dim, bias=False)

    def forward(self, x):
        return self.w2(F.silu(self.w1(x)) * self.w3(x))


class TransformerBlock(nn.Module):
    def __init__(self, layer_id: int, args: ModelArgs):
        super().__init__()

        self.attention = Attention(args)
        self.feed_forward = FeedForward(
            dim=args.dim,
            hidden_dim=4 * args.dim,
            multiple_of=args.multiple_of,
            ffn_dim_multiplier=args.ffn_dim_multiplier,
        )
        self.layer_id = layer_id
        self.attention_norm = RMSNorm(args.dim, eps=args.norm_eps)
        self.ffn_norm = RMSNorm(args.dim, eps=args.norm_eps)

    def forward(
        self,
        x: torch.Tensor,
        start_pos: int,
        freqs_cis: torch.Tensor,
        mask: Optional[torch.Tensor],
    ):
        h = x + self.attention(self.attention_norm(x),
                               start_pos, freqs_cis, mask)
        out = h + self.feed_forward(self.ffn_norm(h))
        return out


class EloHead(nn.Module):
    def __init__(self, params: ModelArgs):
        super().__init__()
        self.params = params
        self.norm = RMSNorm(params.dim, eps=params.norm_eps)
        self.output = nn.Linear(params.dim, params.elo_pred_size, bias=False)

    def forward(self, h):
        h = self.output(self.norm(h)).float()
        if self.params.gaussian_elo:
            # make sure variance is non-negative
            h[:, :, 1] = torch.exp(h[:, :, 1])
        return h


class MoveHead(nn.Module):
    def __init__(self, params: ModelArgs):
        super().__init__()
        self.norm = RMSNorm(params.dim, eps=params.norm_eps)
        self.output = nn.Linear(params.dim, params.vocab_size, bias=False)

    def forward(self, h: torch.Tensor):
        return self.output(self.norm(h)).float()


class Transformer(nn.Module):
    def __init__(self, params: ModelArgs):
        super().__init__()
        self.params = params
        self.vocab_size = params.vocab_size
        self.n_layers = params.n_layers

        self.tok_embeddings = nn.Embedding(params.vocab_size, params.dim)

        self.layers = torch.nn.ModuleList()
        for layer_id in range(params.n_layers):
            self.layers.append(TransformerBlock(layer_id, params))

        self.norm = RMSNorm(params.dim, eps=params.norm_eps)
        tcdim = params.n_timecontrol_heads * params.dim
        self.preproc = nn.Linear(params.dim, tcdim, bias=False)

        if params.elo_pred_size > 0:
            self.elo_heads = nn.ModuleList()
            for _ in range(params.n_timecontrol_heads):
                self.elo_heads.append(EloHead(params))

        if params.predict_move:
            if params.n_elo_heads > 1:
                self.white_heads = nn.ModuleList()
                self.black_heads = nn.ModuleList()
                for _ in range(params.n_timecontrol_heads):
                    welo_heads = nn.ModuleList()
                    belo_heads = nn.ModuleList()
                    for _ in range(params.n_elo_heads):
                        welo_heads.append(MoveHead(params))
                        belo_heads.append(MoveHead(params))
                    self.white_heads.append(welo_heads)
                    self.black_heads.append(belo_heads)
            else:
                self.move_heads = nn.ModuleList()
                for _ in range(params.n_timecontrol_heads):
                    elo_heads = nn.ModuleList()
                    for _ in range(params.n_elo_heads):
                        elo_heads.append(MoveHead(params))
                    self.move_heads.append(elo_heads)

        self.freqs_cis = precompute_freqs_cis(
            params.dim // params.n_heads,
            params.max_seq_len * 2,
            params.rope_theta,
        )

    def _reshape_timecontrol(self, h):
        bs, seqlen, _ = h.shape
        h = h.reshape(bs, seqlen, self.params.n_timecontrol_heads, -1)
        return h

    def _get_elo_pred(self, h: torch.Tensor):
        if self.params.elo_pred_size > 0:
            h_outs = []
            for i in range(self.params.n_timecontrol_heads):
                h_out = self.elo_heads[i](h[:, :, i])
                h_outs.append(h_out)
            return torch.stack(h_outs, 2)
        else:
            return None

    def _get_move_pred(self, h: torch.Tensor):
        if self.params.predict_move:
            tc_outs = []
            for i in range(self.params.n_timecontrol_heads):
                elo_outs = []
                for j in range(self.params.n_elo_heads):
                    if self.params.n_elo_heads > 1:
                        w_out = self.white_heads[i][j](h[:, :, i])
                        b_out = self.black_heads[i][j](h[:, :, i])
                        elo_outs.append(torch.stack([w_out, b_out], dim=2))
                    else:
                        h_out = self.move_heads[i][j](h[:, :, i, None])
                        elo_outs.append(h_out)
                tc_outs.append(torch.stack(elo_outs, dim=2))
            return torch.stack(tc_outs, dim=2)
        else:
            return None

    def forward(self, tokens: torch.Tensor, start_pos: int = 0):
        _, seqlen = tokens.shape
        h = self.tok_embeddings(tokens)

        self.freqs_cis = self.freqs_cis.to(h.device)
        freqs_cis = self.freqs_cis[start_pos: start_pos + seqlen]

        mask = None
        if seqlen > 1:
            mask = torch.full((seqlen, seqlen), float(
                "-inf"), device=tokens.device)
            mask = torch.triu(mask, diagonal=1)

        for layer in self.layers:
            h = layer(h, start_pos, freqs_cis, mask)
        h = self.norm(h)

        h = F.silu(self.preproc(h))
        h = self._reshape_timecontrol(h)
        return self._get_move_pred(h), self._get_elo_pred(h)

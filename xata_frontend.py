import argparse
from xata.client import XataClient
import wget
import os
from base64 import b64encode, b64decode
from math import ceil
from subprocess import Popen

parser = argparse.ArgumentParser(
    formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument(
    "--download_model",
    action="store_true",
    help="download weights.ckpt for model_id",
    default=False,
)
parser.add_argument(
    "--upload_model",
    help="upload weights.ckpt for model_id; if the record does not exist, it will be created",
    action="store_true",
)
parser.add_argument(
    "--model_id", help="id for model weights entry", default=None)
parser.add_argument(
    "--model_path", help="path to ckpt file for uploading or saving", default='weights.ckpt')
parser.add_argument("--upload_cfg", action="store_true",
                    help="upload config.yml")
parser.add_argument('--chunk_size', default=int(2e9),
                    type=int, help='model chunk-size')
parser.add_argument("--download_cfg", action="store_true",
                    help="download config.yml")
parser.add_argument("--cfg_id", help="id for config file entry", default=None)
parser.add_argument(
    "--cfg_path", help="path to config yml for uploading or saving", default="config.yml")


def main():
    args = parser.parse_args()
    xata = XataClient()

    if args.upload_cfg:
        assert args.cfg_id, "cfg_id is required"
        with open(args.cfg_path) as f:
            cfg = f.read()
        rec = xata.records().upsert("config", args.cfg_id, {"config": cfg})
        print(rec)
    elif args.download_cfg:
        assert args.cfg_id, "cfg_id required"
        rec = xata.records().get("config", args.cfg_id, columns=["config"])
        with open(args.cfg_path, "w") as f:
            f.write(rec["config"])

    if args.upload_model:
        assert args.model_id, "model_id required"

        print("encoding model...")
        with open(args.model_path, "rb") as f:
            mdlzip = b64encode(f.read())

        print("uploading model...")

        mid = args.model_id
        nparts = int(ceil(len(mdlzip)/args.chunk_size))
        rec = xata.records().upsert(
            "model",
            mid,
            {"weights_multi": []},
        )
        print(rec)
        for i in range(nparts):
            pid = f'{mid}_{i+1}_of_{nparts}'
            start = int(i*args.chunk_size)
            end = min(int((i+1)*args.chunk_size), len(mdlzip))

            try:
                rec = xata.files().put_item(
                    "model", mid, "weights_multi", pid, mdlzip[start:end])
                print(rec)
            except Exception as e:
                print(e)

    elif args.download_model:
        if args.model_id is None:
            resp = xata.data().query('model', {"filter": {"current": True}})
            assert len(
                resp['records']) == 1, 'zero or multiple records with current=True'
            model_id = resp['records'][0]['id']
        else:
            model_id = args.model_id
        print(model_id)

        rec = xata.records().get("model", model_id,
                                 columns=["weights.signedUrl", 'weights_multi.signedUrl'])

        if 'weights' in rec:
            urls = [rec['weights']['signedUrl']]
        else:
            nparts = len(rec['weights_multi'])
            pids = [f'{model_id}_{i+1}_of_{nparts}' for i in range(nparts)]
            rec = xata.records().update('model', model_id, {
                'weights_multi': [{'id': pid, 'signedUrlTimeout': 6000} for pid in pids]})
            rec = xata.records().get("model", model_id,
                                     columns=['weights_multi.signedUrl'])
            urls = [part['signedUrl'] for part in rec['weights_multi']]

        procs = []
        for i, url in enumerate(urls):
            fn = f'part_{i}.tmp'
            p = Popen(['wget', url, '-O', fn])
            procs.append((p, fn))

        parts = [bytes() for _ in procs]
        for i, (p, fn) in enumerate(procs):
            p.wait()
            with open(fn, "rb") as f:
                part = f.read()
            os.remove(fn)
            parts[i] = part

        mdl = bytes()
        for part in parts:
            mdl += part
        mdl = b64decode(mdl)

        with open(args.model_path, "wb") as f:
            f.write(mdl)


if __name__ == "__main__":
    main()

import argparse
from xata.client import XataClient
import wget
import os
from base64 import b64encode, b64decode

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
parser.add_argument("--download_cfg", action="store_true",
                    help="download config.yml")
parser.add_argument("--cfg_id", help="id for config file entry", default=None)
parser.add_argument(
    "--cfg_path", help="path to config yml for uploading or saving", default="config.yml")

if __name__ == "__main__":
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
            mdlzip = b64encode(f.read()).decode("utf-8")

        print("uploading model...")
        rec = xata.records().upsert(
            "model",
            args.model_id,
            {"weights": {"name": "weights.ckpt", "base64Content": ""}},
        )
        rec = xata.files().put("model", args.model_id, "weights", mdlzip)
        print(rec)

    elif args.download_model:
        if args.model_id is None:
            resp = xata.data().query('model', {"filter": {"current": True}})
            print(resp)
            assert len(
                resp['records']) == 1, 'zero or multiple records with current=True'
            model_id = resp['records'][0]['id']
        else:
            model_id = args.model_id
        print(model_id)
        rec = xata.records().get("model", model_id,
                                 columns=["weights.signedUrl"])
        fn = wget.download(rec["weights"]["signedUrl"])
        with open(fn, "rb") as f:
            mdl = b64decode(f.read())
        os.remove(fn)
        with open(args.model_path, "wb") as f:
            f.write(mdl)

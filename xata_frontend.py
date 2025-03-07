import argparse
from xata.client import XataClient
import wget
import os
from base64 import b64encode, b64decode

parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
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
parser.add_argument("--model_id", help="id for model weights entry", default=None)
parser.add_argument("--model_dir", help="where to save weights.ckpt", default=".")
parser.add_argument("--upload_cfg", action="store_true", help="upload config.yml")
parser.add_argument("--download_cfg", action="store_true", help="download config.yml")
parser.add_argument("--cfg_id", help="id for config file entry", default=None)
parser.add_argument("--cfg_dir", help="where to save config.yml", default=".")

if __name__ == "__main__":
    args = parser.parse_args()
    xata = XataClient()

    if args.download_cfg:
        assert args.cfg_id, "cfg_id required"
        rec = xata.records().get("config", args.cfg_id, columns=["config"])
        with open(os.path.join(args.cfg_dir, "config.yml"), "w") as f:
            f.write(rec["config"])

    if args.upload_model:
        assert args.model_id, "model_id required"

        print("encoding model...")
        with open(os.path.join(args.model_dir, "weights.ckpt"), "rb") as f:
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
        assert args.model_id, "model_id required"
        rec = xata.records().get("model", args.model_id, columns=["weights.signedUrl"])
        fn = wget.download(rec["weights"]["signedUrl"])
        with open(fn, "rb") as f:
            mdl = b64decode(f.read())
        os.remove(fn)
        with open(os.path.join(args.model_dir, "weights.ckpt"), "wb") as f:
            f.write(mdl)

from lib import lichess
import os
import yaml
from lib.models import get_config
import logging
import pathlib

dn = pathlib.Path(__file__).parent.resolve()
with open(os.path.join(dn, "lib", "versioning.yml")) as version_file:
    versioning_info = yaml.safe_load(version_file)
__version__ = versioning_info["lichess_bot_version"]

config = get_config(os.path.join(dn, "config.yml"))
logging_level = logging.INFO
max_retries = config.engine.online_moves.max_retries

li = lichess.Lichess(config.token, config.url,
                     __version__, logging_level, max_retries)
li.upgrade_to_bot_account()

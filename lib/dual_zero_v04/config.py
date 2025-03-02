import yaml


class Config:
    def __init__(self, cfgdict):
        self.__dict__ = cfgdict

    def __getattribute__(self, attr, d=None):
        if attr == "__dict__":
            return super().__getattribute__(attr)

        cur, *rem = attr.split(".")
        if len(rem) == 0:
            val = super().__getattribute__(attr)
            if isinstance(val, dict):
                return Config(val)
            else:
                return val
        else:
            if d is None:
                d = self.__dict__
            return self.__getattribute__(rem, d[cur])

    def save(self, fn):
        with open(fn, "w") as f:
            yaml.dump(self.__dict__, f)

    def __str__(self, indent=0):
        lines = []
        for k, v in self.__dict__.items():
            if isinstance(v, dict):
                lines.extend(
                    ["\t" * indent + f"{k}:", Config(v).__str__(indent=indent + 1)]
                )
            else:
                lines.append("\t" * indent + f"{k}: {v}")
        return "\n".join(lines)

    def items(self):
        return self.__dict__.items()


def test():
    with open("cfg.yml") as f:
        cfg = yaml.load(f, Loader=yaml.CLoader)
    config = Config(cfg)
    print(f"datadir: {config.datadir}")
    print(f"elo loss: {config.elo_params.loss}")
    config.commit = "coolcommit"
    print(f"commit: {config.commit}")
    elo_params = config.elo_params
    print(f"elo params:\n{elo_params}")
    print(f"config:\n{config}")
    config.save("testcfg.yml")


if __name__ == "__main__":
    test()


def get_config(ymlfn):
    with open(ymlfn) as f:
        cfgdict = yaml.load(f, Loader=yaml.CLoader)
    return Config(cfgdict)

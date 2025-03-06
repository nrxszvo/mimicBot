import subprocess
import argparse
import os

parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument(
    "--tokenfile",
    default=os.path.expanduser("~/.githubaccesstoken"),
    help="text file containing github acccess token",
)

parser.add_argument("user", help="username for login")
parser.add_argument("remote", help="remote host server/ip, e.g. 192.168.0.0")
parser.add_argument("myname", help='for git config, e.g. "John Doe"')
parser.add_argument("myemail", help="for git config, e.g. johndoe@email.com")


if __name__ == "__main__":
    args = parser.parse_args()

    myname = f'\\"{args.myname}\\"'
    myemail = f'\\"{args.myemail}\\"'

    def scp(fn, dest):
        return f"scp -i ~/.ssh/gcloud {fn} {args.user}@{args.remote}:{dest};"

    def ssh(cmds):
        return f"ssh -i ~/.ssh/gcloud {args.user}@{args.remote} {cmds};"

    def exists(fn):
        p = subprocess.Popen(
            f"ssh -i ~/.ssh/gcloud {args.user}@{args.remote} test -f {fn}",
            shell=True,
        )
        p.wait()
        return p.returncode == 0

    with open(args.tokenfile) as f:
        token = f'\\"{f.readline().rstrip()}\\"'

    subprocess.call(
        scp("provision.sh", "~")
        + scp("~/.ghtoken", "~")
        + ssh(
            f'"chmod 755 provision.sh; GHTOKEN={token} MYNAME={myname} MYEMAIL={myemail} sh provision.sh"'
        )
        + scp("config.yml", "~/git/mimicBot"),
        shell=True,
    )
    if exists(f"/home/{args.user}/git/mimicBot/lib/dual_zero_v04/weights.ckpt"):
        print("weights.ckpt already present; not resending")
    else:
        subprocess.call(
            scp(
                "../mimicChess/trained_models/dual_zero_v04/weights.ckpt",
                "~/git/mimicBot/lib/dual_zero_v04",
            ),
            shell=True,
        )

import subprocess
import argparse
import os

parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument(
    "--tokenfile",
    default=os.path.expanduser("~/.githubaccesstoken"),
    help="text file containing github acccess token",
)

parser.add_argument("remote", help="remote host login, e.g. ubuntu@192.168.0.0")
parser.add_argument("myname", help='for git config, e.g. "John Doe"')
parser.add_argument("myemail", help="for git config, e.g. johndoe@email.com")

args = parser.parse_args()

myname = f'\\"{args.myname}\\"'
myemail = f'\\"{args.myemail}\\"'

with open(args.tokenfile) as f:
    token = f'\\"{f.readline().rstrip()}\\"'


def scp(fn, dest):
    return f"scp -i ~/.ssh/gcloud {fn} {args.remote}:{dest};"


def ssh(cmds):
    return f"ssh -i ~/.ssh/gcloud {args.remote} {cmds};"


subprocess.call(
    scp("provision.sh", "~")
    + scp("~/.ghtoken", "~")
    + ssh(
        f'"chmod 755 provision.sh; GHTOKEN={token} MYNAME={myname} MYEMAIL={myemail} sh provision.sh"'
    )
    + scp("config.yml", "~/git/mimicBot")
    + scp(
        "../mimicChess/trained_models/dual_zero_v04/weights.ckpt",
        "~/git/mimicBot/lib/dual_zero_v04",
    ),
    shell=True,
)

# scpcmd = f"scp -i ~/.ssh/gcloud provision.sh {args.remote}:~; scp -i ~/.ssh/gcloud ~/.ghtoken {args.remote}:~"
# sshcmd = f'ssh -i ~/.ssh/gcloud {args.remote} "chmod 755 provision.sh; GHTOKEN={token} MYNAME={myname} MYEMAIL={myemail} sh provision.sh"'
# cmd = f"{scpcmd} && {sshcmd}"
# print("\t" + cmd)
# subprocess.call(cmd, shell=True)

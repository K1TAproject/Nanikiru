import argparse
from pathlib import Path
import tkinter as tk
from .app import Monitor


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('directory',nargs='?',type=Path,default=Path(__file__).resolve().parents[1]/'data/external-replays/v2-round1-20261113')
    args=parser.parse_args();root=tk.Tk();Monitor(root,args.directory);root.mainloop()


if __name__=='__main__':main()

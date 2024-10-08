import asyncio, argparse, concurrent.futures, gzip, random, signal, sys
import chess, chess.pgn
import cdbsearch
from io import StringIO
from multiprocessing import freeze_support, active_children
from collections import deque

CDB_EGTB = 7


def wrapcdbsearch(
    epd,
    depthLimit,
    timeLimit,
    concurrency,
    evalDecay,
    cursedWins,
    TBsearch,
    proveMates,
    user,
    suppressErrors,
):
    old_stdout = sys.stdout
    sys.stdout = mystdout = StringIO()
    try:
        asyncio.run(
            cdbsearch.cdbsearch(
                epd=epd,
                depthLimit=depthLimit,
                timeLimit=timeLimit,
                concurrency=concurrency,
                evalDecay=evalDecay,
                cursedWins=cursedWins,
                TBsearch=TBsearch,
                proveMates=proveMates,
                user=user,
                suppressErrors=suppressErrors,
            )
        )
    except Exception as ex:
        print(f' error: while searching EPD "{epd}" caught exception "{ex}"')
    sys.stdout = old_stdout
    return mystdout.getvalue()


def open_file_rt(filename):
    # allow reading text files either plain or in gzip format
    open_func = gzip.open if filename.endswith(".gz") else open
    return open_func(filename, "rt")


def load_epdlist(filename):
    epdlist = []
    with open_file_rt(filename) as f:
        for line in f:
            line = line.strip()
            if line:
                if line.startswith("#"):  # ignore comments
                    continue
                line = line.split(";")[0]  # ignore epd opcodes
                line = line.replace("startpos", chess.STARTING_FEN[:-3])
                epd, _, moves = line.partition("moves")
                epd = epd.split()[:6]  # include potential move counters
                if len(epd) == 6 and not (epd[4].isnumeric() and epd[5].isnumeric()):
                    epd = epd[:4]
                epd = " ".join(epd)
                epdMoves = " moves"
                for m in moves.split():
                    if (
                        len(m) < 4
                        or len(m) > 5
                        or not {m[0], m[2]}.issubset(set("abcdefgh"))
                        or not {m[1], m[3]}.issubset(set("12345678"))
                        or (len(m) == 5 and not m[4] in "qrbn")
                    ):
                        break
                    epdMoves += f" {m}"
                if epdMoves != " moves":
                    epd += epdMoves
                epdlist.append(epd)
    return epdlist


def load_epds(filename, excludeFile=None, plyBegin=-1, plyEnd=None, TBsearch=False):
    """returns a list of unique EPDs found in the given file"""
    epdlist = []
    if filename.endswith(".pgn") or filename.endswith(".pgn.gz"):
        pgn = open_file_rt(filename)
        while True:
            game = chess.pgn.read_game(pgn)
            if game is None:
                break
            epd = game.board().fen()  # include potential move counters
            epdMoves = " moves"
            for m in game.mainline_moves():
                epdMoves += f" {m}"
            if epdMoves != " moves":
                epd += epdMoves
            epdlist.append(epd)
        print(f"Loaded {len(epdlist)} (opening) lines from file {filename}.")
    else:
        epdlist = load_epdlist(filename)
        print(f"Loaded {len(epdlist)} (extended) EPDs from file {filename}.")
        if excludeFile:
            excludelist = load_epdlist(excludeFile)
            epdlist = list(set(epdlist) - set(excludelist))
            print(
                f"Kept {len(epdlist)} (extended) EPDs after excluding EPDs from file {excludeFile}."
            )

    epds = set()  # use a set to filter duplicates
    for epd in epdlist:
        epd, _, moves = epd.partition(" moves")
        moves = [None] + moves.split()  # to be able to use plyBegin=0 for epd
        plyB = (
            0
            if plyBegin is None
            else max(0, plyBegin + len(moves))
            if plyBegin < 0
            else min(plyBegin, len(moves))
        )
        plyE = (
            len(moves)
            if plyEnd is None
            else max(0, plyEnd + len(moves))
            if plyEnd < 0
            else min(plyEnd, len(moves))
        )
        board = chess.Board(epd)
        for ply, m in enumerate(moves):
            if m is not None:
                epd += f" {m}"
                board.push(chess.Move.from_uci(m))
            if not TBsearch and chess.popcount(board.occupied) <= CDB_EGTB:
                break
            if plyB <= ply and ply < plyE:
                epds.add(epd)
            elif ply >= plyE:
                break
            if m is None:
                epd += " moves"
    epds = list(epds)

    print(f"Prepared {len(epds)} unique EPDs for exploration.")
    return epds


class TaskCounter:
    def __init__(self):
        self._counter = 0

    def inc(self):
        self._counter += 1

    def dec(self, fn):
        self._counter -= 1

    def get(self):
        return self._counter


if __name__ == "__main__":
    freeze_support()
    argParser = argparse.ArgumentParser(
        description="Invoke cdbsearch for positions loaded from a file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    argParser.add_argument(
        "filename",
        help="""PGN file if suffix is .pgn(.gz), o/w a file with FENs/EPDs. The latter may use the extended "['startpos'|FEN] moves m1 m2 m3" syntax.""",
    )
    argParser.add_argument(
        "--excludeFile",
        help="A file with FENs/EPDs that should not be loaded from filename.",
    )
    argParser.add_argument(
        "--plyBegin",
        help="Ply in each line of filename from which positions will be searched by cdbsearch. A value of 0 corresponds to the starting FEN without any moves played. Negative values count from the back, as per the Python standard.",
        type=int,
        default=-1,
    )
    argParser.add_argument(
        "--plyEnd",
        help="Ply in each line of filename until which positions will be searched by cdbsearch. A value of None means including the final move of the line.",
        type=int,
        default=None,
    )
    argParser.add_argument(
        "--shuffle",
        action="store_true",
        help="Shuffle the positions to be searched randomly.",
    )
    argParser.add_argument(
        "--depthLimit",
        help="Argument passed to cdbsearch.",
        type=int,
        default=5,
    )
    argParser.add_argument(
        "--timeLimit",
        help="Argument passed to cdbsearch.",
        type=float,
        default=None,
    )
    argParser.add_argument(
        "--concurrency",
        help="Argument passed to cdbsearch.",
        type=int,
        default=16,
    )
    argParser.add_argument(
        "--evalDecay",
        help="Argument passed to cdbsearch.",
        type=int,
        default=2,
    )
    argParser.add_argument(
        "--cursedWins",
        action="store_true",
        help="Argument passed to cdbsearch.",
    )
    argParser.add_argument(
        "--TBsearch",
        action="store_true",
        help="Argument passed to cdbsearch.",
    )
    argParser.add_argument(
        "--proveMates",
        action="store_true",
        help="Argument passed to cdbsearch.",
    )
    argParser.add_argument(
        "--user",
        help="Argument passed to cdbsearch.",
    )
    argParser.add_argument(
        "--suppressErrors",
        action="store_true",
        help="Argument passed to cdbsearch.",
    )
    argParser.add_argument(
        "--bulkConcurrency",
        help="Number of concurrent processes running cdbsearch.",
        type=int,
        default=4,
    )
    argParser.add_argument(
        "--forever",
        action="store_true",
        help="Pass positions from filename to cdbsearch in an infinite loop, increasing depthLimit by one after each completed cycle.",
    )
    argParser.add_argument(
        "--maxDepthLimit",
        help="Upper bound for dynamically increasing depthLimit.",
        type=int,
        default=None,
    )
    argParser.add_argument(
        "--reload",
        action="store_true",
        help="Reload positions from filename when tasks for new cycle are needed.",
    )
    args = argParser.parse_args()

    def on_sigint(signal, frame):
        print("Received signal to terminate. Killing sub-processes.", flush=True)
        for child in active_children():
            child.kill()
        print("Done.", flush=True)
        sys.exit(1)

    # Install signal handlers.
    signal.signal(signal.SIGINT, on_sigint)
    signal.signal(signal.SIGTERM, on_sigint)
    try:
        signal.signal(signal.SIGQUIT, on_sigint)
    except:
        # Windows does not have SIGQUIT.
        pass
    try:
        signal.signal(signal.SIGBREAK, on_sigint)
    except:
        # Linux does not have SIGBREAK.
        pass

    executor = concurrent.futures.ProcessPoolExecutor(max_workers=args.bulkConcurrency)
    print(f"Positions to be explored with concurrency {args.bulkConcurrency}.")

    task, tasks = None, deque()
    taskCounter = TaskCounter()
    first = True
    epdIdx, epds = 0, []

    while True:
        if epdIdx == len(epds):
            # First loop, or we arrived at the end of the list: in that case see if we cycle or break.
            if first or args.forever:
                if first or args.reload:
                    try:
                        epds = load_epds(
                            args.filename,
                            args.excludeFile,
                            args.plyBegin,
                            args.plyEnd,
                            args.TBsearch,
                        )
                        if args.shuffle:
                            random.shuffle(epds)
                    except Exception:
                        if first:
                            raise
                        else:
                            print(
                                f"Error while trying to reload file {args.filename}. Continue with old EPD list."
                            )
                if first:
                    depthLimit = args.depthLimit
                    first = False
                else:
                    depthLimit += 1
                if args.maxDepthLimit is not None:
                    depthLimit = min(depthLimit, args.maxDepthLimit)
                epdIdx = 0
            elif task is None and len(tasks) == 0:
                break
        else:
            # Add some more tasks to the list if few are pending
            if taskCounter.get() < 2 * args.bulkConcurrency:
                epd = epds[epdIdx]
                future = executor.submit(
                    wrapcdbsearch,
                    epd=epd,
                    depthLimit=depthLimit,
                    timeLimit=args.timeLimit,
                    concurrency=args.concurrency,
                    evalDecay=args.evalDecay,
                    cursedWins=args.cursedWins,
                    TBsearch=args.TBsearch,
                    proveMates=args.proveMates,
                    user=args.user,
                    suppressErrors=args.suppressErrors,
                )
                taskCounter.inc()
                future.add_done_callback(taskCounter.dec)
                tasks.append(
                    (
                        epds,
                        epdIdx,
                        depthLimit,
                        future,
                    )
                )
                epdIdx += 1

        if task is None:
            if len(tasks) > 0:
                task = tasks.popleft()
                print(
                    "=" * 72
                    + f'\nAwaiting results for exploration of EPD "{task[0][task[1]]}" ({task[1] + 1} / {len(task[0])}) to depth {task[2]} ... ',
                    flush=True,
                )
        else:
            # See if we have a result, if not continue.
            try:
                print(task[3].result(timeout=0.01), flush=True)
                task = None
            except concurrent.futures.TimeoutError:
                pass
            except Exception as ex:
                print(f' error: caught exception "{ex}"')
                task = None

    executor.shutdown()
    print(f"Done processing {args.filename}.")

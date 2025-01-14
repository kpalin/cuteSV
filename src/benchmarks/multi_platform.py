import sys
import argparse
import logging
import time


def pase_info(seq):
    info = {"SVLEN": 0, "END": 0, "SVTYPE": "", "RE": 0, "CHR2": ""}
    for i in seq.split(";"):
        if i.split("=")[0] in ["SVLEN", "END", "RE"]:
            try:
                info[i.split("=")[0]] = abs(int(float(i.split("=")[1])))
            except Exception:
                pass
        if i.split("=")[0] in ["CHR2"]:
            info[i.split("=")[0]] = i.split("=")[1]
        if i.split("=")[0] in ["SVTYPE"]:
            info[i.split("=")[0]] = i.split("=")[1][0:3]

    return info


def phase_GT(seq):
    i = seq.split(":")
    if i[0] in ["0/1", "1/0"]:
        return "het"
    elif i[0] == "1/1":
        return "hom"
    else:
        return "unknown"


def load_callset(path):
    callset = dict()
    file = open(path, "r")
    for line in file:
        seq = line.strip("\n").split("\t")
        if seq[0][0] == "#":
            continue

        chr = seq[0]
        pos = int(seq[1])
        info = pase_info(seq[7])

        if info["SVTYPE"] in ["DEL", "INS", "DUP", "INV"]:
            if info["SVTYPE"] not in callset:
                callset[info["SVTYPE"]] = dict()
            if info["SVLEN"] == 0:
                info["SVLEN"] = info["END"] - pos + 1

            if chr not in callset[info["SVTYPE"]]:
                callset[info["SVTYPE"]][chr] = list()
            callset[info["SVTYPE"]][chr].append(
                [pos, info["END"], info["SVLEN"], phase_GT(seq[9]), [0, 0]]
            )

        if info["SVTYPE"] == "BND":
            if seq[4][0] == "]":
                form = "]]N"
                chr2 = seq[4].split(":")[0][1:]
                pos2 = int(seq[4].split(":")[1][:-2])
            elif seq[4][0] == "[":
                form = "[[N"
                chr2 = seq[4].split(":")[0][1:]
                pos2 = int(seq[4].split(":")[1][:-2])
            else:
                if seq[4][1] == "]":
                    form = "N]]"
                    chr2 = seq[4].split(":")[0][2:]
                    pos2 = int(seq[4].split(":")[1][:-1])
                else:
                    form = "N[["
                    chr2 = seq[4].split(":")[0][2:]
                    pos2 = int(seq[4].split(":")[1][:-1])
            if info["SVTYPE"] not in callset:
                callset[info["SVTYPE"]] = dict()
            if info["END"] == 0:
                info["CHR2"] = chr2
                info["END"] = pos2

            if chr not in callset[info["SVTYPE"]]:
                callset[info["SVTYPE"]][chr] = list()
            callset[info["SVTYPE"]][chr].append(
                [pos, info["CHR2"], info["END"], form, phase_GT(seq[9]), [0, 0]]
            )

    file.close()
    return callset


def eva_record(call_A, call_B, bias, offect, tag1, tag2):
    # call_A 0/1
    # call_B 1/1
    for svtype in call_A:
        if svtype not in call_B:
            continue

        for chr in call_A[svtype]:
            if chr not in call_B[svtype]:
                continue

            for i in call_A[svtype][chr]:
                for j in call_B[svtype][chr]:
                    if svtype == "INS":
                        if (
                            abs(i[0] - j[0]) <= offect
                            and float(min(i[2], j[2]) / max(i[2], j[2])) >= bias
                        ):
                            i[-1][tag1] = 1
                            j[-1][tag2] = 1
                    elif svtype == "BND":
                        if i[1] == j[1] and i[3] == j[3]:
                            if (
                                abs(i[0] - j[0]) <= offect
                                and abs(i[2] - j[2]) <= offect
                            ):
                                i[-1][tag1] = 1
                                j[-1][tag2] = 1
                    else:
                        if (
                            max(i[0] - offect, j[0]) <= min(i[1] + offect, j[1])
                            and float(min(i[2], j[2]) / max(i[2], j[2])) >= bias
                        ):
                            i[-1][tag1] = 1
                            j[-1][tag2] = 1


def statistics(callset, a, b, c):
    for svtype in callset:
        record = 0
        record_000 = 0
        record_010 = 0
        record_001 = 0
        record_011 = 0
        for chr in callset[svtype]:
            for i in callset[svtype][chr]:
                record += 1
                if i[-1][0] == 0 and i[-1][1] == 0:
                    record_000 += 1
                if i[-1][0] == 1 and i[-1][1] == 0:
                    record_010 += 1
                if i[-1][0] == 0 and i[-1][1] == 1:
                    record_001 += 1
                if i[-1][0] == 1 and i[-1][1] == 1:
                    record_011 += 1

        logging.info("%s number of %s:\t%d" % (svtype, a, record))
        logging.info("Only %s:\t%d" % (a, record_000))
        logging.info("%s and %s:\t%d" % (a, b, record_010))
        logging.info("%s and %s:\t%d" % (a, c, record_001))
        logging.info("%s and %s and %s:\t%d" % (a, b, c, record_011))


def main_ctrl(args):
    logging.info("Load SV callset of selected caller.")

    clr_callset = load_callset(args.c1)
    ont_callset = load_callset(args.c2)
    ccs_callset = load_callset(args.c3)

    logging.info("Comparing...")
    eva_record(clr_callset, ont_callset, args.bias, args.offect, 0, 0)
    eva_record(clr_callset, ccs_callset, args.bias, args.offect, 1, 0)
    eva_record(ont_callset, ccs_callset, args.bias, args.offect, 1, 1)

    logging.info("Final results:")
    statistics(clr_callset, "CLR", "ONT", "CCS")
    statistics(ont_callset, "ONT", "CLR", "CCS")
    statistics(ccs_callset, "CCS", "CLR", "ONT")


def main(argv):
    args = parseArgs(argv)
    setupLogging(False)
    # print args
    starttime = time.time()
    main_ctrl(args)
    logging.info("Finished in %0.2f seconds." % (time.time() - starttime))


USAGE = """\
	Evaluate SV callset generated by cuteSV
"""


def parseArgs(argv):
    parser = argparse.ArgumentParser(
        prog="Trio_eval",
        description=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("c1", type=str, help="PacBio callset")
    parser.add_argument("c2", type=str, help="ONT callset")
    parser.add_argument("c3", type=str, help="High confidence callset")
    parser.add_argument(
        "-b", "--bias", help="Bias of overlaping.[%(default)s]", default=0.7, type=float
    )
    parser.add_argument(
        "-o",
        "--offect",
        help="Offect of translocation overlaping.[%(default)s]",
        default=1000,
        type=int,
    )
    args = parser.parse_args(argv)
    return args


def setupLogging(debug=False):
    logLevel = logging.DEBUG if debug else logging.INFO
    logFormat = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(stream=sys.stderr, level=logLevel, format=logFormat)
    logging.info("Running %s" % " ".join(sys.argv))


if __name__ == "__main__":
    main(sys.argv[1:])

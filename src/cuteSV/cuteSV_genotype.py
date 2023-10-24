import logging
from cuteSV.cuteSV_Description import Generation_VCF_header
from math import log10
import numpy as np

err = 0.1
prior = float(1 / 3)
Genotype = ["0/0", "0/1", "1/1"]


def log10sumexp(log10_probs):
    # Normalization of Genotype likelihoods
    m = max(log10_probs)
    return m + log10(sum(pow(10.0, x - m) for x in log10_probs))


def normalize_log10_probs(log10_probs):
    # Adjust the Genotype likelihoods
    log10_probs = np.array(log10_probs)
    lse = log10sumexp(log10_probs)
    return np.minimum(log10_probs - lse, 0.0)


def rescale_read_counts(c0, c1, max_allowed_reads=100):
    """Ensures that n_total <= max_allowed_reads, rescaling if necessary."""
    Total = c0 + c1
    if Total > max_allowed_reads:
        c0 = int(max_allowed_reads * float(c0 / Total))
        c1 = max_allowed_reads - c0
    return c0, c1


def cal_GL(c0, c1):
    # Approximate adjustment of events with larger read depth
    c0, c1 = rescale_read_counts(c0, c1)
    # original genotype likelihood
    # ori_GL00 = np.float64(pow((1-err), c0)*pow(err, c1)*comb(c0+c1,c0)*(1-prior)/2)
    # ori_GL11 = np.float64(pow(err, c0)*pow((1-err), c1)*comb(c0+c1,c0)*(1-prior)/2)
    # ori_GL01 = np.float64(pow(0.5, c0+c1)*comb(c0+c1,c0)*prior)

    ori_GL00 = np.float64(pow((1 - err), c0) * pow(err, c1) * (1 - prior) / 2)
    ori_GL11 = np.float64(pow(err, c0) * pow((1 - err), c1) * (1 - prior) / 2)
    ori_GL01 = np.float64(pow(0.5, c0 + c1) * prior)

    # normalized genotype likelihood
    prob = list(
        normalize_log10_probs([log10(ori_GL00), log10(ori_GL01), log10(ori_GL11)])
    )
    GL_P = [pow(10, i) for i in prob]
    PL = [int(np.around(-10 * log10(i))) for i in GL_P]
    GQ = [
        int(-10 * log10(GL_P[1] + GL_P[2])),
        int(-10 * log10(GL_P[0] + GL_P[2])),
        int(-10 * log10(GL_P[0] + GL_P[1])),
    ]
    QUAL = abs(np.around(-10 * log10(GL_P[0]), 1))

    return (
        Genotype[prob.index(max(prob))],
        "%d,%d,%d" % (PL[0], PL[1], PL[2]),
        max(GQ),
        QUAL,
    )


def cal_CIPOS(std, num):
    pos = int(1.96 * std / num**0.5)
    return "-%d,%d" % (pos, pos)


def threshold_ref_count(num):
    if num <= 2:
        return 20 * num
    elif 3 <= num <= 5:
        return 9 * num
    elif 6 <= num <= 15:
        return 7 * num
    else:
        return 5 * num


def count_coverage(chr, s, e, f, read_count, up_bound, itround):
    status = 0
    iteration = 0
    primary_num = 0
    for i in f.fetch(chr, s, e):
        iteration += 1
        if i.flag not in [0, 16]:
            continue
        primary_num += 1
        if i.reference_start < s and i.reference_end > e:
            read_count.add(i.query_name)
            if len(read_count) >= up_bound:
                status = 1
                break
        if iteration >= itround:
            if float(primary_num / iteration) <= 0.2:
                status = 1
            else:
                status = -1
            break

    return status


def overlap_cover(svs_list, reads_list):
    # [(10024, 12024), (89258, 91258), ...]
    # [[10000, 10468, 0, 'm54238_180901_011437/52298335/ccs'], [10000, 17490, 1, 'm54238_180901_011437/44762027/ccs'], ...]
    sort_list = list()
    idx = 0
    for i in reads_list:
        sort_list.append([i[0], 1, idx, i[2], i[3]])
        sort_list.append([i[1], 2, idx, i[2], i[3]])
        idx += 1
    idx = 0
    for i in svs_list:
        sort_list.append([i[0], 3, idx])
        sort_list.append([i[1], 0, idx])
        idx += 1
    sort_list = sorted(sort_list, key=lambda x: (x[0], x[1]))
    svs_set = set()
    read_set = set()
    overlap_dict = dict()
    cover_dict = dict()
    for node in sort_list:
        if node[1] == 1:  # set2(read) left
            read_set.add(node[2])
            for x in svs_set:
                if svs_list[x][1] == node[0]:
                    continue
                if x not in overlap_dict:
                    overlap_dict[x] = set()
                overlap_dict[x].add(node[2])
        elif node[1] == 2:  # set2(read) right
            read_set.remove(node[2])
        elif node[1] == 3:  # set1(sv) left
            svs_set.add(node[2])
            overlap_dict[node[2]] = set()
            for x in read_set:
                overlap_dict[node[2]].add(x)
            cover_dict[node[2]] = set()
            for x in read_set:
                cover_dict[node[2]].add(x)
        elif node[1] == 0:  # set1(sv) right
            svs_set.remove(node[2])
            temp_set = set()
            for x in read_set:
                temp_set.add(x)
            cover_dict[node[2]] = cover_dict[node[2]] & temp_set
    cover2_dict = dict()
    iteration_dict = dict()
    primary_num_dict = dict()
    for idx in cover_dict:
        iteration_dict[idx] = len(overlap_dict[idx])
        primary_num_dict[idx] = 0
        for x in overlap_dict[idx]:
            if reads_list[x][2] == 1:
                primary_num_dict[idx] += 1
        cover2_dict[idx] = set()
        for x in cover_dict[idx]:
            if reads_list[x][2] == 1:
                cover2_dict[idx].add(reads_list[x][3])
    # duipai(svs_list, reads_list, iteration_dict, primary_num_dict, cover2_dict)
    return iteration_dict, primary_num_dict, cover2_dict


def assign_gt(iteration_dict, primary_num_dict, cover_dict, read_id_dict):
    assign_list = list()
    for idx in read_id_dict:
        iteration = iteration_dict[idx]
        primary_num = primary_num_dict[idx]
        read_count = cover_dict[idx]
        DR = 0
        for query in read_count:
            if query not in read_id_dict[idx]:
                DR += 1
        GT, GL, GQ, QUAL = cal_GL(DR, len(read_id_dict[idx]))
        assign_list.append([len(read_id_dict[idx]), DR, GT, GL, GQ, QUAL])
    return assign_list


def duipai(svs_list, reads_list, iteration_dict, primary_num_dict, cover2_dict):
    # [(10024, 12024), (89258, 91258), ...]
    # [[10000, 10468, 0, 'm54238_180901_011437/52298335/ccs'], [10000, 17490, 1, 'm54238_180901_011437/44762027/ccs'], ...]
    print("start duipai")
    idx = 0
    correct_num = 0
    bb = set()
    for i in svs_list:
        overlap = set()
        primary_num = 0
        iteration = 0
        for j in reads_list:
            if (j[0] <= i[0] and j[1] > i[0]) or (i[0] <= j[0] < i[1]):
                iteration += 1
                if j[2] == 1:
                    primary_num += 1
                    if i[0] >= j[0] and i[1] <= j[1]:
                        overlap.add(j[3])
        flag = 0
        if iteration != iteration_dict[idx]:
            print(
                "Iteration error %d:%d(now) %d(ans)"
                % (idx, iteration_dict[idx], iteration)
            )
        if primary_num != primary_num_dict[idx]:
            print(
                "Primary_num error %d:%d(now) %d(ans)"
                % (idx, primary_num_dict[idx], primary_num)
            )
        if len(overlap) == len(cover2_dict[idx]):
            flag += 1
        if len(overlap - cover2_dict[idx]) == 0:
            flag += 1
        if len(cover2_dict[idx] - overlap) == 0:
            flag += 1
        if flag != 3:
            print(idx)
            print(overlap)
            print(cover2_dict[idx])
            print(overlap - cover2_dict[idx])
        else:
            correct_num += 1
        idx += 1
    print("Correct iteration %d" % (correct_num))


def generate_output(args, semi_result, contigINFO, argv, ref_g):
    """
    Generation of VCF format file.
    VCF version: 4.2
    """

    # genotype_trigger = TriggerGT[args.genotype]

    svid = dict()
    svid["INS"] = 0
    svid["DEL"] = 0
    svid["BND"] = 0
    svid["DUP"] = 0
    svid["INV"] = 0

    file = open(args.output, "w")
    action = args.genotype
    Generation_VCF_header(file, contigINFO, args.sample, argv)
    file.write(
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t%s\n" % (args.sample)
    )
    for variant in semi_result:
        if (
            variant[1] in {"INS", "DEL", "DUP", "INV"}
            and abs(int(float(variant[3]))) > args.max_size
            and args.max_size != -1
        ):
            logging.debug(
                "Skipping due to size of %d: %s",
                abs(int(float(variant[3]))),
                str(variant),
            )
            continue
        if variant[1] in ["DEL", "INS"]:
            if abs(int(float(variant[3]))) < args.min_size:
                logging.debug(
                    "Skipping due to short size of %d: %s",
                    abs(int(float(variant[3]))),
                    str(variant),
                )
                continue
            output_INS_DEL(args, ref_g, svid, file, action, variant)
        elif variant[1] == "DUP":
            output_DUP(args, ref_g, svid, file, action, variant)
        elif variant[1] == "INV":
            output_INV(args, ref_g, svid, file, action, variant)
        else:
            # BND
            # info_list = "{PRECISION};SVTYPE={SVTYPE};CHR2={CHR2};END={END};RE={RE};RNAMES={RNAMES}".format(
            logging.debug(
                "Outputting %s:%d  %s as BND.",
                variant[0],
                int(variant[2]) + 1,
                variant[1],
            )
            output_BND(args, ref_g, svid, file, action, variant)


def output_BND(args, ref_g, svid, file, action, variant):
    info_list = "{PRECISION};SVTYPE={SVTYPE};RE={RE};RNAMES={RNAMES}".format(
        PRECISION="IMPRECISE" if variant[7] == "0/0" else "PRECISE",
        SVTYPE="BND",
        # CHR2 = i[3],
        # END = str(int(i[4]) + 1),
        RE=variant[5],
        RNAMES=variant[11] if args.report_readid else "NULL",
    )
    if action:
        try:
            info_list += ";AF=" + str(
                round(int(variant[5]) / (int(variant[5]) + int(variant[6])), 4)
            )
        except:
            info_list += ";AF=."
    if variant[10] == ".":
        filter_lable = "PASS"
    else:
        filter_lable = "PASS" if float(variant[10]) >= 5.0 else "q5"
    try:
        reff = str(ref_g[variant[0]][int(variant[2])])
    except:
        reff = "N"
    file.write(
        "{CHR}\t{POS}\t{ID}\t{REF}\t{ALT}\t{QUAL}\t{PASS}\t{INFO}\t{FORMAT}\t{GT}:{DR}:{RE}:{PL}:{GQ}\n".format(
            CHR=variant[0],
            POS=str(int(variant[2]) + 1),
            ID="cuteSV.%s.%d" % ("BND", svid["BND"]),
            REF=reff,
            ALT=variant[1],
            INFO=info_list,
            FORMAT="GT:DR:DV:PL:GQ",
            GT=variant[7],
            DR=variant[6],
            RE=variant[5],
            PL=variant[8],
            GQ=variant[9],
            QUAL=variant[10],
            PASS=filter_lable,
        )
    )
    svid["BND"] += 1


def output_INV(args, ref_g, svid, file, action, variant):
    cal_end = int(variant[2]) + 1 + abs(int(float(variant[3])))
    info_list = "{PRECISION};SVTYPE={SVTYPE};SVLEN={SVLEN};END={END};RE={RE};STRAND={STRAND};RNAMES={RNAMES}".format(
        PRECISION="IMPRECISE" if variant[6] == "0/0" else "PRECISE",
        SVTYPE=variant[1],
        SVLEN=variant[3],
        END=str(cal_end),
        RE=variant[4],
        STRAND=variant[7],
        RNAMES=variant[11] if args.report_readid else "NULL",
    )
    if action:
        try:
            info_list += ";AF=" + str(
                round(int(variant[4]) / (int(variant[4]) + int(variant[5])), 4)
            )
        except:
            info_list += ";AF=."
    if variant[10] == ".":
        filter_lable = "PASS"
    else:
        filter_lable = "PASS" if float(variant[10]) >= 5.0 else "q5"
    file.write(
        "{CHR}\t{POS}\t{ID}\t{REF}\t{ALT}\t{QUAL}\t{PASS}\t{INFO}\t{FORMAT}\t{GT}:{DR}:{RE}:{PL}:{GQ}\n".format(
            CHR=variant[0],
            POS=str(int(variant[2]) + 1),
            ID="cuteSV.%s.%d" % (variant[1], svid[variant[1]]),
            REF=str(ref_g[variant[0]][int(variant[2])]),
            ALT="<%s>" % (variant[1]),
            INFO=info_list,
            FORMAT="GT:DR:DV:PL:GQ",
            GT=variant[6],
            DR=variant[5],
            RE=variant[4],
            PL=variant[8],
            GQ=variant[9],
            QUAL=variant[10],
            PASS=filter_lable,
        )
    )
    svid[variant[1]] += 1


def output_DUP(args, ref_g, svid, file, action, variant):
    cal_end = int(variant[2]) + 1 + abs(int(float(variant[3])))
    info_list = "{PRECISION};SVTYPE={SVTYPE};SVLEN={SVLEN};END={END};RE={RE};STRAND=-+;RNAMES={RNAMES}".format(
        PRECISION="IMPRECISE" if variant[6] == "0/0" else "PRECISE",
        SVTYPE=variant[1],
        SVLEN=variant[3],
        END=str(cal_end),
        RE=variant[4],
        RNAMES=variant[10] if args.report_readid else "NULL",
    )
    if action:
        try:
            info_list += ";AF=" + str(
                round(int(variant[4]) / (int(variant[4]) + int(variant[5])), 4)
            )
        except:
            info_list += ";AF=."
    if variant[9] == ".":
        filter_lable = "PASS"
    else:
        filter_lable = "PASS" if float(variant[9]) >= 5.0 else "q5"
    file.write(
        "{CHR}\t{POS}\t{ID}\t{REF}\t{ALT}\t{QUAL}\t{PASS}\t{INFO}\t{FORMAT}\t{GT}:{DR}:{RE}:{PL}:{GQ}\n".format(
            CHR=variant[0],
            POS=str(int(variant[2]) + 1),
            ID="cuteSV.%s.%d" % (variant[1], svid[variant[1]]),
            REF=str(ref_g[variant[0]][int(variant[2])]),
            ALT="<%s>" % (variant[1]),
            INFO=info_list,
            FORMAT="GT:DR:DV:PL:GQ",
            GT=variant[6],
            DR=variant[5],
            RE=variant[4],
            PL=variant[7],
            GQ=variant[8],
            QUAL=variant[9],
            PASS=filter_lable,
        )
    )
    svid[variant[1]] += 1


def output_INS_DEL(args, ref_g, svid, file, action, i):
    if i[1] == "INS":
        cal_end = int(i[2])
    else:
        cal_end = int(i[2]) + abs(int(float(i[3])))
    info_list = "{PRECISION};SVTYPE={SVTYPE};SVLEN={SVLEN};END={END};CIPOS={CIPOS};CILEN={CILEN};RE={RE};RNAMES={RNAMES}".format(
        PRECISION="IMPRECISE" if i[8] == "0/0" else "PRECISE",
        SVTYPE=i[1],
        SVLEN=i[3],
        END=str(cal_end),
        CIPOS=i[5],
        CILEN=i[6],
        RE=i[4],
        RNAMES=i[12] if args.report_readid else "NULL",
    )
    if action:
        try:
            info_list += ";AF=" + str(round(int(i[4]) / (int(i[4]) + int(i[7])), 4))
        except:
            info_list += ";AF=."
    if i[1] == "DEL":
        info_list += ";STRAND=+-"
    if i[11] == "." or i[11] == None:
        filter_lable = "PASS"
    else:
        filter_lable = "PASS" if float(i[11]) >= 5.0 else "q5"

    # Infer alleles
    if i[1] == "INS":
        REF = str(ref_g[i[0]][max(int(i[2]) - 1, 0)])
        ALT = str(ref_g[i[0]][max(int(i[2]) - 1, 0)]) + i[13]
    elif i[1] == "DEL":
        if abs(float(i[3])) <= (args.max_ref_allele):
            REF = str(ref_g[i[0]][max(int(i[2]) - 1, 0) : int(i[2]) - int(i[3])])
            ALT = str(ref_g[i[0]][max(int(i[2]) - 1, 0)])
        else:
            #logging.debug("Not reporting long reference allele for %s", str(i[:4]))
            REF = str(ref_g[i[0]][max(int(i[2]) - 1, 0)])
            ALT = "<DEL>"
    else:
        raise ValueError(args=i)

    file.write(
        "{CHR}\t{POS}\t{ID}\t{REF}\t{ALT}\t{QUAL}\t{PASS}\t{INFO}\t{FORMAT}\t{GT}:{DR}:{RE}:{PL}:{GQ}\n".format(
            CHR=i[0],
            POS=str(int(i[2])),
            ID="cuteSV.%s.%d" % (i[1], svid[i[1]]),
            REF=REF,
            ALT=ALT,
            INFO=info_list,
            FORMAT="GT:DR:DV:PL:GQ",
            GT=i[8],
            DR=i[7],
            RE=i[4],
            PL=i[9],
            GQ=i[10],
            QUAL=i[11],
            PASS=filter_lable,
        )
    )
    svid[i[1]] += 1


def generate_pvcf(args, result, contigINFO, argv, ref_g):
    file = open(args.output, "w")
    Generation_VCF_header(file, contigINFO, args.sample, argv)
    file.write(
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t%s\n" % (args.sample)
    )
    # [chrom(0), sv_start, genotype(2), sv_type, sv_end(4), CIPOS, CILEN(6), [gt_re, DR, GT(new), GL, GQ, QUAL], rname(8), svid, ref(10), alts, sv_strand(12), seq]
    for i in result:
        if i == []:
            continue
        # if i[7][5] == '.,.':
        #     print(i)
        if i[7][5] == "." or i[7][5] == None:
            filter_lable = "PASS"
        else:
            filter_lable = "PASS" if float(i[7][5]) >= 2.5 else "q5"
        if i[3] == "INS":
            if abs(i[4]) > args.max_size and args.max_size != -1:
                continue
            """
            if i[11] == '<INS>':
                ref = str(ref_g[i[0]][max(i[1]-1, 0)])
                alt = str(ref_g[i[0]][max(i[1]-1, 0)]) + i[13]
            else:
                ref = i[10]
                alt = i[11]
            """
            ref = str(ref_g[i[0]][max(i[1] - 1, 0)])
            alt = i[11]
            info_list = "{PRECISION};SVTYPE={SVTYPE};SVLEN={SVLEN};END={END};CIPOS={CIPOS};CILEN={CILEN};RE={RE};RNAMES={RNAMES}".format(
                PRECISION="IMPRECISE" if i[2] == "0/0" else "PRECISE",
                SVTYPE=i[3],
                SVLEN=i[4],
                END=i[1],
                CIPOS=i[5],
                CILEN=i[6],
                RE=i[7][0],
                RNAMES=i[8] if args.report_readid else "NULL",
            )
            try:
                info_list += ";AF=" + str(round(i[7][0] / (i[7][0] + i[7][1]), 4))
            except:
                info_list += ";AF=."
            file.write(
                "{CHR}\t{POS}\t{ID}\t{REF}\t{ALT}\t{QUAL}\t{PASS}\t{INFO}\t{FORMAT}\t{GT}:{DR}:{RE}:{PL}:{GQ}\n".format(
                    CHR=i[0],
                    POS=i[1],
                    ID=i[9],
                    REF=ref,
                    ALT=alt,
                    QUAL=i[7][5],
                    PASS=filter_lable,
                    INFO=info_list,
                    FORMAT="GT:DR:DV:PL:GQ",
                    GT=i[2],
                    DR=i[7][1],
                    RE=i[7][0],
                    PL=i[7][3],
                    GQ=i[7][4],
                )
            )
        elif i[3] == "DEL":
            if abs(i[4]) > args.max_size and args.max_size != -1:
                continue
            if i[12] == "<DEL>":
                ref = str(ref_g[i[0]][max(int(i[1]) - 1, 0) : int(i[1]) - int(i[4])])
                alt = str(ref_g[i[0]][max(int(i[1]) - 1, 0)])
            else:
                ref = i[10]
                alt = i[11]
            info_list = "{PRECISION};SVTYPE={SVTYPE};SVLEN={SVLEN};END={END};CIPOS={CIPOS};CILEN={CILEN};RE={RE};RNAMES={RNAMES};STRAND=+-".format(
                PRECISION="IMPRECISE" if i[2] == "0/0" else "PRECISE",
                SVTYPE=i[3],
                SVLEN=-abs(i[4]),
                END=i[1] + abs(i[4]),
                CIPOS=i[5],
                CILEN=i[6],
                RE=i[7][0],
                RNAMES=i[8] if args.report_readid else "NULL",
            )
            try:
                info_list += ";AF=" + str(round(i[7][0] / (i[7][0] + i[7][1]), 4))
            except:
                info_list += ";AF=."
            file.write(
                "{CHR}\t{POS}\t{ID}\t{REF}\t{ALT}\t{QUAL}\t{PASS}\t{INFO}\t{FORMAT}\t{GT}:{DR}:{RE}:{PL}:{GQ}\n".format(
                    CHR=i[0],
                    POS=i[1],
                    ID=i[9],
                    REF=ref,
                    ALT=alt,
                    QUAL=i[7][5],
                    PASS=filter_lable,
                    INFO=info_list,
                    FORMAT="GT:DR:DV:PL:GQ",
                    GT=i[2],
                    DR=i[7][1],
                    RE=i[7][0],
                    PL=i[7][3],
                    GQ=i[7][4],
                )
            )
        elif i[3] == "DUP":
            if abs(i[4] - i[1]) > args.max_size and args.max_size != -1:
                continue
            info_list = "{PRECISION};SVTYPE={SVTYPE};SVLEN={SVLEN};END={END};RE={RE};RNAMES={RNAMES};STRAND=-+".format(
                PRECISION="IMPRECISE" if i[2] == "0/0" else "PRECISE",
                SVTYPE=i[3],
                SVLEN=abs(i[4] - i[1]),
                END=i[4],
                RE=i[7][0],
                RNAMES=i[8] if args.report_readid else "NULL",
            )
            try:
                info_list += ";AF=" + str(round(i[7][0] / (i[7][0] + i[7][1]), 4))
            except:
                info_list += ";AF=."
            file.write(
                "{CHR}\t{POS}\t{ID}\t{REF}\t{ALT}\t{QUAL}\t{PASS}\t{INFO}\t{FORMAT}\t{GT}:{DR}:{RE}:{PL}:{GQ}\n".format(
                    CHR=i[0],
                    POS=i[1],
                    ID=i[9],
                    REF=i[10],
                    ALT=i[11],
                    QUAL=i[7][5],
                    PASS=filter_lable,
                    INFO=info_list,
                    FORMAT="GT:DR:DV:PL:GQ",
                    GT=i[2],
                    DR=i[7][1],
                    RE=i[7][0],
                    PL=i[7][3],
                    GQ=i[7][4],
                )
            )
        elif i[3] == "INV":
            if abs(i[4] - i[1]) > args.max_size and args.max_size != -1:
                continue
            info_list = "{PRECISION};SVTYPE={SVTYPE};SVLEN={SVLEN};END={END};RE={RE};RNAMES={RNAMES}".format(
                PRECISION="IMPRECISE" if i[2] == "0/0" else "PRECISE",
                SVTYPE=i[3],
                SVLEN=i[4] - i[1],
                END=i[4],
                RE=i[7][0],
                RNAMES=i[8] if args.report_readid else "NULL",
            )
            if i[12] != ".":
                info_list += ";STRAND=" + i[12]
            try:
                info_list += ";AF=" + str(round(i[7][0] / (i[7][0] + i[7][1]), 4))
            except:
                info_list += ";AF=."
            file.write(
                "{CHR}\t{POS}\t{ID}\t{REF}\t{ALT}\t{QUAL}\t{PASS}\t{INFO}\t{FORMAT}\t{GT}:{DR}:{RE}:{PL}:{GQ}\n".format(
                    CHR=i[0],
                    POS=i[1],
                    ID=i[9],
                    REF=i[10],
                    ALT=i[11],
                    QUAL=i[7][5],
                    PASS=filter_lable,
                    INFO=info_list,
                    FORMAT="GT:DR:DV:PL:GQ",
                    GT=i[2],
                    DR=i[7][1],
                    RE=i[7][0],
                    PL=i[7][3],
                    GQ=i[7][4],
                )
            )
        else:
            # BND
            info_list = "{PRECISION};SVTYPE={SVTYPE};RE={RE};RNAMES={RNAMES}".format(
                PRECISION="IMPRECISE" if i[2] == "0/0" else "PRECISE",
                SVTYPE=i[3],
                RE=i[7][0],
                RNAMES=i[8] if args.report_readid else "NULL",
            )
            try:
                info_list += ";AF=" + str(round(i[7][0] / (i[7][0] + i[7][1]), 4))
            except:
                info_list += ";AF=."
            """
            if ':' in i[15]:
                info_list += ";CHR2={CHR2};END={END}".format(
                    CHR2 = i[15].split(':')[0],
                    END = i[15].split(':')[1])
            """
            file.write(
                "{CHR}\t{POS}\t{ID}\t{REF}\t{ALT}\t{QUAL}\t{PASS}\t{INFO}\t{FORMAT}\t{GT}:{DR}:{RE}:{PL}:{GQ}\n".format(
                    CHR=i[0],
                    POS=str(i[1]),
                    ID=i[9],
                    REF=i[10],
                    ALT=i[11],
                    QUAL=i[7][5],
                    PASS=filter_lable,
                    INFO=info_list,
                    FORMAT="GT:DR:DV:PL:GQ",
                    GT=i[2],
                    DR=i[7][1],
                    RE=i[7][0],
                    PL=i[7][3],
                    GQ=i[7][4],
                )
            )


def load_valuable_chr(path):
    valuable_chr = dict()
    valuable_chr["DEL"] = list()
    valuable_chr["DUP"] = list()
    valuable_chr["INS"] = list()
    valuable_chr["INV"] = list()
    valuable_chr["TRA"] = dict()

    for svtype in ["DEL", "DUP", "INS", "INV"]:
        file = open("%s%s.sigs" % (path, svtype), "r")
        for line in file:
            chr = line.strip("\n").split("\t")[1]
            if chr not in valuable_chr[svtype]:
                valuable_chr[svtype].append(chr)
        file.close()
        valuable_chr[svtype].sort()

    file = open("%s%s.sigs" % (path, "TRA"), "r")
    for line in file:
        chr1 = line.strip("\n").split("\t")[1]
        chr2 = line.strip("\n").split("\t")[4]

        if chr1 not in valuable_chr["TRA"]:
            valuable_chr["TRA"][chr1] = list()
        if chr2 not in valuable_chr["TRA"][chr1]:
            valuable_chr["TRA"][chr1].append(chr2)

    file.close()
    for chr1 in valuable_chr["TRA"]:
        valuable_chr["TRA"][chr1].sort()

    return valuable_chr


def load_bed(bed_file, Task_list):
    # Task_list: [[chr, start, end], ...]
    bed_regions = dict()
    if bed_file != None:
        # only consider regions in BED file
        with open(bed_file, "r") as f:
            for line in f:
                seq = line.strip().split("\t")
                if seq[0] not in bed_regions:
                    bed_regions[seq[0]] = list()
                bed_regions[seq[0]].append((int(seq[1]) - 1000, int(seq[2]) + 1000))
        region_list = [[] for i in range(len(Task_list))]
        for chrom in bed_regions:
            bed_regions[chrom].sort(key=lambda x: (x[0], x[1]))
            for item in bed_regions[chrom]:
                for i in range(len(Task_list)):
                    if chrom == Task_list[i][0]:
                        if (
                            Task_list[i][1] <= item[0] and Task_list[i][2] > item[0]
                        ) or item[0] <= Task_list[i][1] < item[1]:
                            region_list[i].append(item)
        assert len(region_list) == len(Task_list), "parse bed file error"
        return region_list
    else:
        return None

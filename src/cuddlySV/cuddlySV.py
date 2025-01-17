#!/usr/bin/env python

from pathlib import Path
from typing import Any
import pysam
import pyfastx
from .Description import WorkDir, parseArgs, setupLogging
from multiprocessing import Pool
from .CommandRunner import exe
from .split_signal import organize_split_signal

# from resolution_type import *
from .resolveINV import run_inv
from .resolveTRA import run_tra
from .resolveINDEL import run_ins, run_del
from .resolveDUP import run_dup
from .genotype import (
    generate_output,
    generate_pvcf,
    load_bed,
)
from .forcecalling import force_calling_chrom
import os
import logging
import sys
import time
import gc


def get_cigar_ref_length(cigar: str) -> int:
    "Return the length of reference consumed by the cigar string"
    import re

    cig_parts = re.findall(r"(\d+)(\D)", cigar)

    consumed_ref = sum(int(length) for length, op in cig_parts if op in "MDN=X")

    return consumed_ref


def is_1d2_read(aligned_segment: pysam.AlignedSegment, overlap_threshold=0.95) -> bool:
    """Check if the aligned_segment is false 1d2 read, i.e. aligned in two parts, overlapping, in
    opposite strands.

    Return True, if aligned_segment has supplementary alignment which is shorter, opposite strand
    and overlapping for over overlap_threshold"""
    # Extract the reference name, start, and end coordinates of the aligned segment
    ref_name = aligned_segment.reference_name
    start = aligned_segment.reference_start
    end = aligned_segment.reference_end

    # Extract the SA tag and split it into supplementary alignments
    try:
        sa_tag = aligned_segment.get_tag("SA").rstrip(";").split(";")
    except KeyError:
        return False

    # Check for each supplementary alignment if it overlaps with the aligned segment
    for sa_entry in sa_tag:
        fields = sa_entry.split(",")
        sa_ref_name = fields[0]
        sa_start = int(fields[1]) - 1  # to zero based like pysam
        sa_is_forward = 1 if fields[2] == "+" else 0
        sa_cigar = fields[3]
        _sa_mapq = int(fields[4])

        if sa_ref_name != ref_name:
            # Different contig
            continue
        if sa_is_forward == aligned_segment.is_forward:
            # Same strand.
            continue

        sa_ref_len = get_cigar_ref_length(sa_cigar)
        sa_end = sa_start + sa_ref_len

        if aligned_segment.reference_length > sa_ref_len:
            # The supplementary alignment is shorter
            continue

        # Calculate the length of the overlap
        overlap_length = min(end, sa_end) - max(start, sa_start)

        # Calculate the percentage of overlap
        ref_len = min(end - start, sa_ref_len)
        overlap_percentage = overlap_length / ref_len

        if overlap_percentage >= overlap_threshold:
            return True

    return False


def get_query_name(read: pysam.AlignedSegment) -> str:
    """Get aligned read name annotated with source of th read

    Args:
        read (pysam.AlignedSegment): Alignment info from pysam/bam/cram

    Returns:
        str: Read name in format 'query_name:read_group:haplotype:phase_set
    """
    try:
        rg = read.get_tag("RG")
    except KeyError:
        rg = ""
    # try:
    #     hp = str(read.get_tag("HP"))
    # except KeyError:
    #     hp = ""
    # try:
    #     ps = str(read.get_tag("PS"))
    # except KeyError:
    #     ps = ""
    read_name = ":".join([read.query_name, rg])
    return read_name


def analysis_inv(ele_1, ele_2, read_name, candidate, SV_size):
    if ele_1[5] == "+":
        # +-
        if ele_1[3] - ele_2[3] >= SV_size:
            if ele_2[0] + 0.5 * (ele_1[3] - ele_2[3]) >= ele_1[1]:
                candidate.append(["++", ele_2[3], ele_1[3], read_name, "INV", ele_1[4]])
                # head-to-head
                # 5'->5'
        if ele_2[3] - ele_1[3] >= SV_size:
            if ele_2[0] + 0.5 * (ele_2[3] - ele_1[3]) >= ele_1[1]:
                candidate.append(["++", ele_1[3], ele_2[3], read_name, "INV", ele_1[4]])
                # head-to-head
                # 5'->5'
    else:
        # -+
        if ele_2[2] - ele_1[2] >= SV_size:
            if ele_2[0] + 0.5 * (ele_2[2] - ele_1[2]) >= ele_1[1]:
                candidate.append(["--", ele_1[2], ele_2[2], read_name, "INV", ele_1[4]])
                # tail-to-tail
                # 3'->3'
        if ele_1[2] - ele_2[2] >= SV_size:
            if ele_2[0] + 0.5 * (ele_1[2] - ele_2[2]) >= ele_1[1]:
                candidate.append(["--", ele_2[2], ele_1[2], read_name, "INV", ele_1[4]])
                # tail-to-tail
                # 3'->3'


def analysis_bnd(ele_1, ele_2, read_name, candidate):
    """
    *********Description*********
    *	TYPE A:		N[chr:pos[	*
    *	TYPE B:		N]chr:pos]	*
    *	TYPE C:		[chr:pos[N	*
    *	TYPE D:		]chr:pos]N	*
    *****************************
    """
    if ele_2[0] - ele_1[1] <= 100:
        if ele_1[5] == "+":
            if ele_2[5] == "+":
                # +&+
                if ele_1[4] < ele_2[4]:
                    candidate.append(
                        ["A", ele_1[3], ele_2[4], ele_2[2], read_name, "TRA", ele_1[4]]
                    )
                    # N[chr:pos[
                else:
                    candidate.append(
                        ["D", ele_2[2], ele_1[4], ele_1[3], read_name, "TRA", ele_2[4]]
                    )
                    # ]chr:pos]N
            else:
                # +&-
                if ele_1[4] < ele_2[4]:
                    candidate.append(
                        ["B", ele_1[3], ele_2[4], ele_2[3], read_name, "TRA", ele_1[4]]
                    )
                    # N]chr:pos]
                else:
                    candidate.append(
                        ["B", ele_2[3], ele_1[4], ele_1[3], read_name, "TRA", ele_2[4]]
                    )
                    # N]chr:pos]
        else:
            if ele_2[5] == "+":
                # -&+
                if ele_1[4] < ele_2[4]:
                    candidate.append(
                        ["C", ele_1[2], ele_2[4], ele_2[2], read_name, "TRA", ele_1[4]]
                    )
                    # [chr:pos[N
                else:
                    candidate.append(
                        ["C", ele_2[2], ele_1[4], ele_1[2], read_name, "TRA", ele_2[4]]
                    )
                    # [chr:pos[N
            else:
                # -&-
                if ele_1[4] < ele_2[4]:
                    candidate.append(
                        ["D", ele_1[2], ele_2[4], ele_2[3], read_name, "TRA", ele_1[4]]
                    )
                    # ]chr:pos]N
                else:
                    candidate.append(
                        ["A", ele_2[3], ele_1[4], ele_1[2], read_name, "TRA", ele_2[4]]
                    )
                    # N[chr:pos[


# TODO:  This function has a bug. Read aligned in 3 parts, large deletion followed by false inversion is not classified as deletion signal.
def analysis_split_read(
    split_read, SV_size, RLength, read_name, candidate, MaxSize, query
):
    """
    read_start	read_end	ref_start	ref_end	chr	strand
    #0			#1			#2			#3		#4	#5
    """
    SP_list = sorted(split_read, key=lambda x: x[0])
    # print(read_name)
    # for i in SP_list:
    # 	print(i)

    # detect INS involoved in a translocation
    trigger_INS_TRA = 0

    # Store Strands of INV

    if len(SP_list) == 2:
        ele_1 = SP_list[0]
        ele_2 = SP_list[1]
        if ele_1[4] == ele_2[4]:
            if ele_1[5] != ele_2[5]:
                analysis_inv(ele_1, ele_2, read_name, candidate, SV_size)

            else:
                # dup & ins & del
                a = 0
                if ele_1[5] == "-":
                    ele_1 = [
                        RLength - SP_list[a + 1][1],
                        RLength - SP_list[a + 1][0],
                    ] + SP_list[a + 1][2:]
                    ele_2 = [
                        RLength - SP_list[a][1],
                        RLength - SP_list[a][0],
                    ] + SP_list[a][2:]
                    query = query[::-1]

                if ele_1[3] - ele_2[2] >= SV_size:
                    # if ele_2[1] - ele_1[1] >= ele_1[3] - ele_2[2]:
                    if ele_2[0] - ele_1[1] >= ele_1[3] - ele_2[2]:
                        candidate.append(
                            [
                                (ele_1[3] + ele_2[2]) / 2,
                                ele_2[0] + ele_1[3] - ele_2[2] - ele_1[1],
                                read_name,
                                str(
                                    query[
                                        ele_1[1]
                                        + int((ele_1[3] - ele_2[2]) / 2) : ele_2[0]
                                        - int((ele_1[3] - ele_2[2]) / 2)
                                    ]
                                ),
                                "INS",
                                ele_2[4],
                            ]
                        )
                    else:
                        candidate.append(
                            [ele_2[2], ele_1[3], read_name, "DUP", ele_2[4]]
                        )

                delta_length = ele_2[0] + ele_1[3] - ele_2[2] - ele_1[1]
                if (
                    ele_1[3] - ele_2[2] < max(SV_size, delta_length / 5)
                    and delta_length >= SV_size
                ):
                    if ele_2[2] - ele_1[3] <= max(100, delta_length / 5) and (
                        delta_length <= MaxSize or MaxSize == -1
                    ):
                        candidate.append(
                            [
                                (ele_2[2] + ele_1[3]) / 2,
                                delta_length,
                                read_name,
                                str(
                                    query[
                                        ele_1[1]
                                        + int((ele_2[2] - ele_1[3]) / 2) : ele_2[0]
                                        - int((ele_2[2] - ele_1[3]) / 2)
                                    ]
                                ),
                                "INS",
                                ele_2[4],
                            ]
                        )
                delta_length = ele_2[2] - ele_2[0] + ele_1[1] - ele_1[3]
                if (
                    ele_1[3] - ele_2[2] < max(SV_size, delta_length / 5)
                    and delta_length >= SV_size
                ):
                    if ele_2[0] - ele_1[1] <= max(100, delta_length / 5) and (
                        delta_length <= MaxSize or MaxSize == -1
                    ):
                        candidate.append(
                            [ele_1[3], delta_length, read_name, "DEL", ele_2[4]]
                        )
        else:
            trigger_INS_TRA = 1
            analysis_bnd(ele_1, ele_2, read_name, candidate)

    else:
        # over three splits
        for a in range(len(SP_list[1:-1])):
            ele_1 = SP_list[a]
            ele_2 = SP_list[a + 1]
            ele_3 = SP_list[a + 2]

            if ele_1[4] == ele_2[4]:
                if ele_2[4] == ele_3[4]:
                    if ele_1[5] == ele_3[5] and ele_1[5] != ele_2[5]:
                        if ele_2[5] == "-":
                            # +-+
                            if (
                                ele_2[0] + 0.5 * (ele_3[2] - ele_1[3]) >= ele_1[1]
                                and ele_3[0] + 0.5 * (ele_3[2] - ele_1[3]) >= ele_2[1]
                            ):
                                # No overlaps in split reads

                                if ele_2[2] >= ele_1[3] and ele_3[2] >= ele_2[3]:
                                    candidate.append(
                                        [
                                            "++",
                                            ele_1[3],
                                            ele_2[3],
                                            read_name,
                                            "INV",
                                            ele_1[4],
                                        ]
                                    )
                                    # head-to-head
                                    # 5'->5'
                                    candidate.append(
                                        [
                                            "--",
                                            ele_2[2],
                                            ele_3[2],
                                            read_name,
                                            "INV",
                                            ele_1[4],
                                        ]
                                    )
                                    # tail-to-tail
                                    # 3'->3'
                        else:
                            # -+-
                            if (
                                ele_1[1] <= ele_2[0] + 0.5 * (ele_1[2] - ele_3[3])
                                and ele_3[0] + 0.5 * (ele_1[2] - ele_3[3]) >= ele_2[1]
                            ):
                                # No overlaps in split reads

                                if (
                                    ele_2[2] - ele_3[3] >= -50
                                    and ele_1[2] - ele_2[3] >= -50
                                ):
                                    candidate.append(
                                        [
                                            "++",
                                            ele_3[3],
                                            ele_2[3],
                                            read_name,
                                            "INV",
                                            ele_1[4],
                                        ]
                                    )
                                    # head-to-head
                                    # 5'->5'
                                    candidate.append(
                                        [
                                            "--",
                                            ele_2[2],
                                            ele_1[2],
                                            read_name,
                                            "INV",
                                            ele_1[4],
                                        ]
                                    )
                                    # tail-to-tail
                                    # 3'->3'

                    if len(SP_list) - 3 == a:
                        if ele_1[5] != ele_3[5]:
                            if ele_2[5] == ele_1[5]:
                                # ++-/--+
                                analysis_inv(
                                    ele_2, ele_3, read_name, candidate, SV_size
                                )
                            else:
                                # +--/-++
                                analysis_inv(
                                    ele_1, ele_2, read_name, candidate, SV_size
                                )

                    if ele_1[5] == ele_3[5] and ele_1[5] == ele_2[5]:
                        # dup & ins & del
                        if ele_1[5] == "-":
                            ele_1 = [
                                RLength - SP_list[a + 2][1],
                                RLength - SP_list[a + 2][0],
                            ] + SP_list[a + 2][2:]
                            ele_2 = [
                                RLength - SP_list[a + 1][1],
                                RLength - SP_list[a + 1][0],
                            ] + SP_list[a + 1][2:]
                            ele_3 = [
                                RLength - SP_list[a][1],
                                RLength - SP_list[a][0],
                            ] + SP_list[a][2:]
                            query = query[::-1]

                        if ele_2[3] - ele_3[2] >= SV_size and ele_2[2] < ele_3[3]:
                            candidate.append(
                                [ele_3[2], ele_2[3], read_name, "DUP", ele_2[4]]
                            )

                        if a == 0:
                            if ele_1[3] - ele_2[2] >= SV_size:
                                candidate.append(
                                    [ele_2[2], ele_1[3], read_name, "DUP", ele_2[4]]
                                )

                        delta_length = ele_2[0] + ele_1[3] - ele_2[2] - ele_1[1]
                        if (
                            ele_1[3] - ele_2[2] < max(SV_size, delta_length / 5)
                            and delta_length >= SV_size
                        ):
                            if ele_2[2] - ele_1[3] <= max(100, delta_length / 5) and (
                                delta_length <= MaxSize or MaxSize == -1
                            ):
                                if ele_3[2] >= ele_2[3]:
                                    candidate.append(
                                        [
                                            (ele_2[2] + ele_1[3]) / 2,
                                            delta_length,
                                            read_name,
                                            str(
                                                query[
                                                    ele_1[1]
                                                    + int(
                                                        (ele_2[2] - ele_1[3]) / 2
                                                    ) : ele_2[0]
                                                    - int((ele_2[2] - ele_1[3]) / 2)
                                                ]
                                            ),
                                            "INS",
                                            ele_2[4],
                                        ]
                                    )
                        delta_length = ele_2[2] - ele_2[0] + ele_1[1] - ele_1[3]
                        if (
                            ele_1[3] - ele_2[2] < max(SV_size, delta_length / 5)
                            and delta_length >= SV_size
                        ):
                            if ele_2[0] - ele_1[1] <= max(100, delta_length / 5) and (
                                delta_length <= MaxSize or MaxSize == -1
                            ):
                                if ele_3[2] >= ele_2[3]:
                                    candidate.append(
                                        [
                                            ele_1[3],
                                            delta_length,
                                            read_name,
                                            "DEL",
                                            ele_2[4],
                                        ]
                                    )

                        if len(SP_list) - 3 == a:
                            ele_1 = ele_2
                            ele_2 = ele_3

                            delta_length = ele_2[0] + ele_1[3] - ele_2[2] - ele_1[1]
                            if (
                                ele_1[3] - ele_2[2] < max(SV_size, delta_length / 5)
                                and delta_length >= SV_size
                            ):
                                if ele_2[2] - ele_1[3] <= max(
                                    100, delta_length / 5
                                ) and (delta_length <= MaxSize or MaxSize == -1):
                                    candidate.append(
                                        [
                                            (ele_2[2] + ele_1[3]) / 2,
                                            delta_length,
                                            read_name,
                                            str(
                                                query[
                                                    ele_1[1]
                                                    + int(
                                                        (ele_2[2] - ele_1[3]) / 2
                                                    ) : ele_2[0]
                                                    - int((ele_2[2] - ele_1[3]) / 2)
                                                ]
                                            ),
                                            "INS",
                                            ele_2[4],
                                        ]
                                    )

                            delta_length = ele_2[2] - ele_2[0] + ele_1[1] - ele_1[3]
                            if (
                                ele_1[3] - ele_2[2] < max(SV_size, delta_length / 5)
                                and ele_2[2] - ele_2[0] + ele_1[1] - ele_1[3] >= SV_size
                            ):
                                if ele_2[0] - ele_1[1] <= max(
                                    100, delta_length / 5
                                ) and (delta_length <= MaxSize or MaxSize == -1):
                                    candidate.append(
                                        [
                                            ele_1[3],
                                            delta_length,
                                            read_name,
                                            "DEL",
                                            ele_2[4],
                                        ]
                                    )

                    if (
                        len(SP_list) - 3 == a
                        and ele_1[5] != ele_2[5]
                        and ele_2[5] == ele_3[5]
                    ):
                        ele_1 = ele_2
                        ele_2 = ele_3
                        ele_3 = None
                    if ele_3 is None or (ele_1[5] == ele_2[5] and ele_2[5] != ele_3[5]):
                        if ele_1[5] == "-":
                            ele_1 = [
                                RLength - SP_list[a + 2][1],
                                RLength - SP_list[a + 2][0],
                            ] + SP_list[a + 2][2:]
                            ele_2 = [
                                RLength - SP_list[a + 1][1],
                                RLength - SP_list[a + 1][0],
                            ] + SP_list[a + 1][2:]
                            query = query[::-1]
                        delta_length = ele_2[0] + ele_1[3] - ele_2[2] - ele_1[1]
                        if (
                            ele_1[3] - ele_2[2] < max(SV_size, delta_length / 5)
                            and delta_length >= SV_size
                        ):
                            if ele_2[2] - ele_1[3] <= max(100, delta_length / 5) and (
                                delta_length <= MaxSize or MaxSize == -1
                            ):
                                candidate.append(
                                    [
                                        (ele_2[2] + ele_1[3]) / 2,
                                        delta_length,
                                        read_name,
                                        str(
                                            query[
                                                ele_1[1]
                                                + int(
                                                    (ele_2[2] - ele_1[3]) / 2
                                                ) : ele_2[0]
                                                - int((ele_2[2] - ele_1[3]) / 2)
                                            ]
                                        ),
                                        "INS",
                                        ele_2[4],
                                    ]
                                )

                        delta_length = ele_2[2] - ele_2[0] + ele_1[1] - ele_1[3]
                        if (
                            ele_1[3] - ele_2[2] < max(SV_size, delta_length / 5)
                            and delta_length >= SV_size
                        ):
                            if ele_2[0] - ele_1[1] <= max(100, delta_length / 5) and (
                                delta_length <= MaxSize or MaxSize == -1
                            ):
                                candidate.append(
                                    [ele_1[3], delta_length, read_name, "DEL", ele_2[4]]
                                )

            else:
                trigger_INS_TRA = 1
                analysis_bnd(ele_1, ele_2, read_name, candidate)

                if len(SP_list) - 3 == a:
                    if ele_2[4] != ele_3[4]:
                        analysis_bnd(ele_2, ele_3, read_name, candidate)

    if len(SP_list) >= 3 and trigger_INS_TRA == 1:
        if SP_list[0][4] == SP_list[-1][4]:
            # print(SP_list[0])
            # print(SP_list[-1])
            if SP_list[0][5] != SP_list[-1][5]:
                pass
            else:
                if SP_list[0][5] == "+":
                    ele_1 = SP_list[0]
                    ele_2 = SP_list[-1]
                else:
                    ele_1 = [
                        RLength - SP_list[-1][1],
                        RLength - SP_list[-1][0],
                    ] + SP_list[-1][2:]
                    ele_2 = [
                        RLength - SP_list[0][1],
                        RLength - SP_list[0][0],
                    ] + SP_list[0][2:]
                    query = query[::-1]
                # print(ele_1)
                # print(ele_2)
                dis_ref = ele_2[2] - ele_1[3]
                dis_read = ele_2[0] - ele_1[1]
                if (
                    dis_ref < 100
                    and dis_read - dis_ref >= SV_size
                    and (dis_read - dis_ref <= MaxSize or MaxSize == -1)
                ):
                    # print(min(ele_2[2], ele_1[3]), dis_read - dis_ref, read_name)
                    candidate.append(
                        [
                            min(ele_2[2], ele_1[3]),
                            dis_read - dis_ref,
                            read_name,
                            str(
                                query[
                                    ele_1[1] + int(dis_ref / 2) : ele_2[0]
                                    - int(dis_ref / 2)
                                ]
                            ),
                            "INS",
                            ele_2[4],
                        ]
                    )

                if dis_ref <= -SV_size:
                    candidate.append([ele_2[2], ele_1[3], read_name, "DUP", ele_2[4]])


def generate_combine_sigs(sigs, Chr_name, read_name, svtype, candidate, merge_dis):
    # for i in sigs:
    # 	print(svtype,i, len(sigs))
    if len(sigs) == 0:
        pass
    elif len(sigs) == 1:
        if svtype == "INS":
            candidate.append(
                [sigs[0][0], sigs[0][1], read_name, sigs[0][2], svtype, Chr_name]
            )
        else:
            candidate.append([sigs[0][0], sigs[0][1], read_name, svtype, Chr_name])
    else:
        temp_sig = sigs[0]
        if svtype == "INS":
            temp_sig += [sigs[0][0]]
            for i in sigs[1:]:
                if i[0] - temp_sig[3] <= merge_dis:
                    temp_sig[1] += i[1]
                    temp_sig[2] += i[2]
                    temp_sig[3] = i[0]
                else:
                    candidate.append(
                        [
                            temp_sig[0],
                            temp_sig[1],
                            read_name,
                            temp_sig[2],
                            svtype,
                            Chr_name,
                        ]
                    )
                    temp_sig = i
                    temp_sig.append(i[0])
            candidate.append(
                [temp_sig[0], temp_sig[1], read_name, temp_sig[2], svtype, Chr_name]
            )
        else:
            temp_sig += [sum(sigs[0])]
            # merge_dis_bias = max([i[1]] for i in sigs)
            for i in sigs[1:]:
                if i[0] - temp_sig[2] <= merge_dis:
                    temp_sig[1] += i[1]
                    temp_sig[2] = sum(i)
                else:
                    candidate.append(
                        [temp_sig[0], temp_sig[1], read_name, svtype, Chr_name]
                    )
                    temp_sig = i
                    temp_sig.append(i[0])
            candidate.append([temp_sig[0], temp_sig[1], read_name, svtype, Chr_name])


def parse_read(
    aligned: pysam.AlignedSegment,
    Chr_name,
    SV_size,
    min_mapq,
    max_split_parts,
    min_read_len,
    min_siglength,
    merge_del_threshold,
    merge_ins_threshold,
    MaxSize,
):
    if aligned.query_length < min_read_len:
        return []
    is_1d2_chimera = is_1d2_read(aligned)

    if is_1d2_chimera:
        return []
    candidate = list()
    Combine_sig_in_same_read_ins = list()
    Combine_sig_in_same_read_del = list()
    from pysam import CSOFT_CLIP, CHARD_CLIP, CMATCH, CINS, CDEL, CEQUAL, CDIFF

    if aligned.mapq >= min_mapq:
        pos_start = aligned.reference_start  # 0-based
        pos_end = aligned.reference_end
        shift_del = 0
        shift_ins = 0
        softclip_left = 0
        softclip_right = 0
        hardclip_left = 0
        hardclip_right = 0
        shift_ins_read = 0
        if aligned.cigar[0][0] == CSOFT_CLIP:
            softclip_left = aligned.cigar[0][1]
        if aligned.cigar[0][0] == CHARD_CLIP:
            hardclip_left = aligned.cigar[0][1]

        for element in aligned.cigar:
            if element[0] in [CMATCH, CEQUAL, CDIFF]:
                shift_del += element[1]
            if (
                element[0] == CDEL and element[1] < min_siglength
            ):  ## changed SV_size to min_siglength
                shift_del += element[1]
            if (
                element[0] == CDEL and element[1] >= min_siglength
            ):  ## changed SV_size to min_siglength
                Combine_sig_in_same_read_del.append([pos_start + shift_del, element[1]])
                shift_del += element[1]

            # calculate offset of an ins sig in read
            if element[0] != CDEL:
                shift_ins_read += element[1]

            if element[0] in [CMATCH, CDEL, CEQUAL, CDIFF]:
                shift_ins += element[1]
            if (
                element[0] == CINS and element[1] >= min_siglength
            ):  ## changed SV_size to min_siglength
                Combine_sig_in_same_read_ins.append(
                    [
                        pos_start + shift_ins,
                        element[1],
                        str(
                            aligned.query_sequence[
                                shift_ins_read
                                - element[1]
                                - hardclip_left : shift_ins_read - hardclip_left
                            ]
                        ),
                    ]
                )

        if aligned.cigar[-1][0] == CSOFT_CLIP:
            softclip_right = aligned.cigar[-1][1]
        if aligned.cigar[-1][0] == CHARD_CLIP:
            hardclip_right = aligned.cigar[-1][1]

        if hardclip_left != 0:
            softclip_left = hardclip_left
        if hardclip_right != 0:
            softclip_right = hardclip_right

    # ************Combine signals in same read********************
    generate_combine_sigs(
        Combine_sig_in_same_read_ins,
        Chr_name,
        get_query_name(aligned),
        "INS",
        candidate,
        merge_ins_threshold,
    )
    generate_combine_sigs(
        Combine_sig_in_same_read_del,
        Chr_name,
        get_query_name(aligned),
        "DEL",
        candidate,
        merge_del_threshold,
    )

    if aligned.flag == 0 or aligned.flag == pysam.FREVERSE:
        # Exclude duplicate, supplementary, secondary, qcfail, paired etc. reads.
        if aligned.mapq >= min_mapq:
            if not aligned.is_reverse:
                primary_info = [
                    softclip_left,
                    aligned.query_length - softclip_right,
                    pos_start,
                    pos_end,
                    Chr_name,
                    "+",
                ]
            else:
                primary_info = [
                    softclip_right,
                    aligned.query_length - softclip_left,
                    pos_start,
                    pos_end,
                    Chr_name,
                    "-",
                ]
        else:
            primary_info = []

        if not is_1d2_chimera:
            # Ignore the false chimeric alignment for the false 1d2 reads
            try:
                SAtag = aligned.get_tag("SA")
            except KeyError:
                SAtag = None
            if SAtag is not None:
                Supplementary_info = SAtag.split(";")[:-1]
                organize_split_signal(
                    primary_info,
                    Supplementary_info,
                    aligned.query_length,
                    SV_size,
                    min_mapq,
                    max_split_parts,
                    get_query_name(aligned),
                    candidate,
                    MaxSize,
                    aligned.query_sequence,
                )
    return candidate


def single_pipe(
    sam_path,
    min_length,
    min_mapq,
    max_split_parts,
    min_read_len,
    temp_dir: Path,
    task,
    min_siglength,
    merge_del_threshold,
    merge_ins_threshold,
    MaxSize,
    bed_regions,
    verbose,
):
    candidate = list()
    reads_info_list = list()
    Chr_name = task[0]
    samfile = pysam.AlignmentFile(sam_path)

    for read in samfile.fetch(Chr_name, task[1], task[2]):
        if read.is_secondary:
            # Skip secondary alignments
            continue
        pos_start = read.reference_start  # 0-based
        pos_end = read.reference_end
        in_bed = False
        if bed_regions is not None:
            for bed_region in bed_regions:
                if pos_end <= bed_region[0] or pos_start >= bed_region[1]:
                    continue
                else:
                    in_bed = True
                    break
        else:
            in_bed = True

        if read.reference_start >= task[1] and in_bed:
            read_candidate = parse_read(
                read,
                Chr_name,
                min_length,
                min_mapq,
                max_split_parts,
                min_read_len,
                min_siglength,
                merge_del_threshold,
                merge_ins_threshold,
                MaxSize,
            )
            candidate.extend(read_candidate)
            if read.mapq >= min_mapq:
                is_primary = 0
                if read.flag in [0, pysam.FREVERSE]:
                    # Read is primary if not paired, secondary supplementary, duplicated or qcfailed.
                    is_primary = 1

                reads_info_list.append(
                    [pos_start, pos_end, is_primary, get_query_name(read)]
                )
    samfile.close()
    # print('finish %s:%d-%d in %f seconds.'%(task[0], task[1], len(reads_info_list), time.time() - start_time))

    if len(candidate) == 0:
        logging.info("Skip %s:%d-%d." % (Chr_name, task[1], task[2]))
        return

    output = temp_dir / ("signatures/_%s_%d_%d.bed" % (Chr_name, task[1], task[2]))
    file = open(output, "w")
    for ele in candidate:
        if len(ele) == 5:
            assert ele[-2] in (
                "DUP",
                "DEL",
            )
            file.write(
                "%s\t%s\t%d\t%d\t%s\n" % (ele[-2], ele[-1], ele[0], ele[1], ele[2])
            )
        elif len(ele) == 7:
            assert ele[-2] == "TRA"
            file.write(
                "%s\t%s\t%s\t%d\t%s\t%d\t%s\n"
                % (ele[-2], ele[-1], ele[0], ele[1], ele[2], ele[3], ele[4])
            )
        elif len(ele) == 6:
            try:
                file.write(
                    "%s\t%s\t%s\t%d\t%d\t%s\n"
                    % (ele[-2], ele[-1], ele[0], ele[1], ele[2], ele[3])
                )
                assert ele[-2] == "INV"
                # INV chr strand pos1 pos2 read_ID
            except Exception:
                assert ele[-2] == "INS"
                file.write(
                    "%s\t%s\t%d\t%d\t%s\t%s\n"
                    % (ele[-2], ele[-1], ele[0], ele[1], ele[2], ele[3])
                )
                # INS chr pos len read_ID seq
    file.close()
    reads_output = temp_dir / (
        "signatures/_%s_%d_%d.reads"
        % (
            Chr_name,
            task[1],
            task[2],
        )
    )
    reads_file = open(reads_output, "w")
    for ele in reads_info_list:
        reads_file.write(
            "%s\t%d\t%d\t%d\t%s\n" % (Chr_name, ele[0], ele[1], ele[2], ele[3])
        )
    reads_file.close()
    logging.info("Finished %s:%d-%d." % (Chr_name, task[1], task[2]))
    gc.collect()


def multi_run_wrapper(args):
    setupLogging(True)
    try:
        return single_pipe(*args)
    except Exception as exc:
        logging.exception("Exception while handing alignments!")
        raise exc


def main_ctrl(args, argv):
    if not os.path.isfile(args.reference):
        raise FileNotFoundError("[Errno 2] No such file: '%s'" % args.reference)
    temporary_dir = WorkDir(args.work_dir)

    # Apologise about the following line. I just can't fix all the silly directory handling here.

    contigINFO, read_group_name = process_bam_file(
        args, temporary_dir.path, temporary_dir.temp_dir_empty()
    )

    #'''
    #'''
    if temporary_dir.temp_dir_empty():
        logging.info("Rebuilding signatures of structural variants.")
        merge_signatures(args, temporary_dir.path)
    else:
        args.retain_work_dir = True
        logging.info(
            "Using signatures of structural variants from %s.", temporary_dir.path
        )
    #'''

    result = list()

    if args.Ivcf is not None:
        # force calling
        logging.warning(
            "Force calling does something very different from denovo calling!"
        )
        result = force_call_genotypes(args, temporary_dir)

    else:
        valuable_chr = temporary_dir.load_valuable_chr()

        logging.info("Clustering structural variants.")
        analysis_pools = Pool(processes=int(args.threads))

        def error_handler(exc, pool=analysis_pools):
            logging.exception("Exception while multiprocessing! Exiting..")
            pool.terminate()
            raise exc

        # +++++DEL+++++
        for chr in valuable_chr["DEL"]:
            para = [
                {
                    "path": temporary_dir,
                    "chr": chr,
                    "svtype": "DEL",
                    "read_count": args.min_support,
                    "threshold_gloab": args.diff_ratio_merging_DEL,
                    "max_cluster_bias": args.max_cluster_bias_DEL,
                    "minimum_support_reads": min(args.min_support, 5),
                    "bam_path": args.input,
                    "action": args.genotype,
                    "gt_round": args.gt_round,
                    "remain_reads_ratio": args.remain_reads_ratio,
                }
            ]
            result.append(
                analysis_pools.map_async(run_del, para, error_callback=error_handler)
            )

        # +++++INS+++++
        for chr in valuable_chr["INS"]:
            para = [
                {
                    "path": temporary_dir,
                    "chr": chr,
                    "svtype": "INS",
                    "read_count": args.min_support,
                    "threshold_gloab": args.diff_ratio_merging_INS,
                    "max_cluster_bias": args.max_cluster_bias_INS,
                    "minimum_support_reads": min(args.min_support, 5),
                    "bam_path": args.input,
                    "action": args.genotype,
                    "gt_round": args.gt_round,
                    "remain_reads_ratio": args.remain_reads_ratio,
                }
            ]
            result.append(
                analysis_pools.map_async(run_ins, para, error_callback=error_handler)
            )

        # +++++INV+++++
        for chr in valuable_chr["INV"]:
            para = [
                {
                    "path": temporary_dir,
                    "chr": chr,
                    "svtype": "INV",
                    "read_count": args.min_support,
                    "max_cluster_bias": args.max_cluster_bias_INV,
                    "sv_size": args.min_size,
                    "bam_path": args.input,
                    "action": args.genotype,
                    "MaxSize": args.max_size,
                    "gt_round": args.gt_round,
                }
            ]
            result.append(
                analysis_pools.map_async(run_inv, para, error_callback=error_handler)
            )

        # +++++DUP+++++
        for chr in valuable_chr["DUP"]:
            para = [
                {
                    "path": temporary_dir,
                    "chr": chr,
                    "read_count": args.min_support,
                    "max_cluster_bias": args.max_cluster_bias_DUP,
                    "sv_size": args.min_size,
                    "bam_path": args.input,
                    "action": args.genotype,
                    "MaxSize": args.max_size,
                    "gt_round": args.gt_round,
                }
            ]
            result.append(
                analysis_pools.map_async(run_dup, para, error_callback=error_handler)
            )

        # +++++TRA+++++
        for chr in valuable_chr["TRA"]:
            for chr2 in valuable_chr["TRA"][chr]:
                para = [
                    {
                        "path": temporary_dir,
                        "chr_1": chr,
                        "chr_2": chr2,
                        "read_count": args.min_support,
                        "overlap_size": args.diff_ratio_filtering_TRA,
                        "max_cluster_bias": args.max_cluster_bias_TRA,
                        "bam_path": args.input,
                        "action": args.genotype,
                        "gt_round": args.gt_round,
                    }
                ]
                result.append(
                    analysis_pools.map_async(
                        run_tra, para, error_callback=error_handler
                    )
                )

        analysis_pools.close()
        analysis_pools.join()
        del valuable_chr

    logging.info("Writing to your output file.")

    logging.info("Loading reference genome...")
    from functools import lru_cache

    class CacheFasta:
        def __init__(self, fasta_name):
            self.__fasta = pyfastx.Fasta(fasta_name)

        @lru_cache(maxsize=1)
        def __getitem__(self, _x):
            return self.__fasta[_x]

        def __getattr__(self, __name: str) -> Any:
            return self.__fasta.__getattr__(__name)

    ref_g = CacheFasta(args.reference)
    # ref_g = pyfastx.Fasta(args.reference)

    if args.Ivcf is not None:
        result = sorted(result, key=lambda x: (x[0], x[1]))
        generate_pvcf(args, result, contigINFO, argv, ref_g)

    else:
        semi_result = list()
        for res in result:
            try:
                semi_result += res.get()[0]
            except Exception as exc:
                raise exc
        # sort SVs by [chr] and [pos]
        semi_result = sorted(semi_result, key=lambda x: (x[0], int(x[2])))

        logging.info("Writing output...")
        generate_output(args, semi_result, contigINFO, argv, ref_g)

    if args.retain_work_dir:
        pass
    else:
        logging.info("Cleaning temporary files.")
        cmd_remove_tempfile = f"rm -r {temporary_dir}/signatures {temporary_dir}/*.sigs"
        exe(cmd_remove_tempfile)


def force_call_genotypes(args, temporary_dir):
    max_cluster_bias_dict = dict()
    max_cluster_bias_dict["INS"] = args.max_cluster_bias_INS
    max_cluster_bias_dict["DEL"] = args.max_cluster_bias_DEL
    max_cluster_bias_dict["DUP"] = args.max_cluster_bias_DUP
    max_cluster_bias_dict["INV"] = args.max_cluster_bias_INV
    max_cluster_bias_dict["TRA"] = args.max_cluster_bias_TRA
    threshold_gloab_dict = dict()
    threshold_gloab_dict["INS"] = args.diff_ratio_merging_INS
    threshold_gloab_dict["DEL"] = args.diff_ratio_merging_DEL

    result = force_calling_chrom(
        args.Ivcf,
        temporary_dir,
        max_cluster_bias_dict,
        threshold_gloab_dict,
        args.gt_round,
        args.threads,
    )

    return result


def process_bam_file(args, temporary_dir: Path, update_temp_data: bool = True):
    samfile = pysam.AlignmentFile(args.input)
    contig_num = len(samfile.get_index_statistics())
    logging.info("The total number of chromosomes: %d" % (contig_num))

    Task_list = list()
    chr_name_list = list()
    contigINFO = list()

    rgs = samfile.header["RG"]
    assert len(rgs) == 1, "Alignment file should have exactly one read group."
    read_group_name = rgs[0]["ID"]

    ref_ = samfile.get_index_statistics()
    for i in ref_:
        chr_name = i[0]
        chr_name_list.append(chr_name)
        local_ref_len = samfile.get_reference_length(chr_name)
        contigINFO.append([chr_name, local_ref_len])
        if local_ref_len < args.batches:
            Task_list.append([chr_name, 0, local_ref_len])
        else:
            pos = 0
            task_round = int(local_ref_len / args.batches)
            for j in range(task_round):
                Task_list.append([chr_name, pos, pos + args.batches])
                pos += args.batches
            if pos < local_ref_len:
                Task_list.append([chr_name, pos, local_ref_len])
    samfile.close()
    bed_regions = load_bed(args.include_bed, Task_list)
    #'''
    if update_temp_data:
        process_alignments(args, temporary_dir, Task_list, bed_regions)
    return (contigINFO, read_group_name)


def process_alignments(args, temporary_dir: Path, Task_list, bed_regions):
    signatures_path = temporary_dir / "signatures/"
    signatures_path.mkdir(parents=True, exist_ok=True)
    logging.info("Signature path '%s'.", str(signatures_path))

    analysis_pools = Pool(processes=int(args.threads))

    def error_handler(exc, pool=analysis_pools):
        logging.exception("Exception while multiprocessing! Exiting..")
        pool.terminate()
        raise exc

    for i in range(len(Task_list)):
        para = [
            (
                args.input,
                args.min_size,
                args.min_mapq,
                args.max_split_parts,
                args.min_read_len,
                temporary_dir,
                Task_list[i],
                args.min_siglength,
                args.merge_del_threshold,
                args.merge_ins_threshold,
                args.max_size,
                None if bed_regions is None else bed_regions[i],
                args.verbose,
            )
        ]
        analysis_pools.map_async(multi_run_wrapper, para, error_callback=error_handler)
    analysis_pools.close()
    analysis_pools.join()


def merge_signatures(args, temporary_dir: Path):
    temporary_dir = str(temporary_dir) + "/"
    cmd_del = (
        "cat %ssignatures/*.bed | grep -w DEL | sort -u -T %s | sort -k 2,2 -k 3,3n -T %s > %sDEL.sigs"
        % (temporary_dir, temporary_dir, temporary_dir, temporary_dir)
    )
    cmd_ins = (
        "cat %ssignatures/*.bed | grep -w INS | sort -u -T %s | sort -k 2,2 -k 3,3n -T %s > %sINS.sigs"
        % (temporary_dir, temporary_dir, temporary_dir, temporary_dir)
    )
    cmd_inv = (
        "cat %ssignatures/*.bed | grep -w INV | sort -u -T %s | sort -k 2,2 -k 3,3 -k 4,4n -T %s > %sINV.sigs"
        % (temporary_dir, temporary_dir, temporary_dir, temporary_dir)
    )
    cmd_tra = (
        "cat %ssignatures/*.bed | grep -w TRA | sort -u -T %s | sort -k 2,2 -k 5,5 -k 3,3 -k 4,4n -T %s > %sTRA.sigs"
        % (temporary_dir, temporary_dir, temporary_dir, temporary_dir)
    )
    cmd_dup = (
        "cat %ssignatures/*.bed | grep -w DUP | sort -u -T %s | sort -k 1,1r -k 2,2 -k 3,4n -T %s > %sDUP.sigs"
        % (temporary_dir, temporary_dir, temporary_dir, temporary_dir)
    )
    cmd_reads = "cat %ssignatures/*.reads > %sreads.sigs" % (
        temporary_dir,
        temporary_dir,
    )

    analysis_pools = Pool(processes=int(args.threads))
    for i in [cmd_ins, cmd_del, cmd_dup, cmd_tra, cmd_inv, cmd_reads]:
        analysis_pools.map_async(exe, (i,))
    analysis_pools.close()
    analysis_pools.join()


def run(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    args = parseArgs(argv)
    setupLogging(args.verbose)
    starttime = time.time()
    main_ctrl(args, argv)
    logging.info("Finished in %0.2f seconds." % (time.time() - starttime))

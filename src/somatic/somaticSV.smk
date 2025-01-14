configfile: "cuddly_somatic.json"


rule all:
    input:
        f"{config['sample_name']}.cuddlysv.somatic.filtered.vcf.gz",


rule somatic_filter:
    input:
        "{tumor}.cuddlysv.somatic.vcf.gz",
    output:
        vcf="{tumor}.cuddlysv.somatic.filtered.vcf.gz",
        idx="{tumor}.cuddlysv.somatic.filtered.vcf.gz.csi",
    shell:
        "bcftools view --write-index -o {output.vcf} -i 'SOMATIC=1' -f PASS {input}"


rule MQ_tags:
    input:
        cram=config["tumor_alignment"],
        somatic_vcf="joint.{tumor}.cuddlysv.vcf.gz",
        somatic_vcf_idx="joint.{tumor}.cuddlysv.vcf.gz.tbi",
    output:
        somatic_MQ_vcf="{tumor}.cuddlysv.somatic.vcf.gz",
        somatic_MQ_vcf_idx="{tumor}.cuddlysv.somatic.vcf.gz.tbi",
    resources:
        mem_mb=60000,
    benchmark:
        "log/mq_tagging_{tumor}.time.txt"
    params:
        ref_fasta=config["reference_fasta"],
    log:
        "log/mq_tagging_{tumor}.log",
    shell:
        """
        add_mapping_tags.py --min_support 3 --verbose -v {input.somatic_vcf} -a {input.cram} -o /dev/stdout 2>{log} |bcftools view -i 'SOMATIC=1' -o {output.somatic_MQ_vcf} ;
        tabix -p vcf {output.somatic_MQ_vcf}
        """


rule join_signatures:
    input:
        tumor_dir=config["tumor_work_dir"],
        normal_dir=config["normal_work_dir"],
    output:
        workdir=temp(directory("merged_work_{tumor}/")),
    resources:
        mem_mb=23000,
    benchmark:
        "log/join_signatures_{tumor}.time.txt"
    shell:
        "merge_work_dirs.sh -o {output.workdir} -t {input.tumor_dir} -n {input.normal_dir}"


rule temp_bgzip_tabix:
    input:
        "{filename}.vcf",
    output:
        temp("{filename}.vcf.gz"),
        temp("{filename}.vcf.gz.tbi"),
    wildcard_constraints:
        filename="joint[.].*",
    shell:
        "bgzip {input};tabix -p vcf {output[0]}"


rule somatic:
    input:
        cram=config["tumor_alignment"],
        workdir="merged_work_{tumor}/",
    output:
        joint_vcf=temp("joint.{tumor}.cuddlysv.vcf"),
    resources:
        mem_mb=51000,
    benchmark:
        "log/merged_{tumor}.time.txt"
    log:
        "log/merged_{tumor}.log",
    threads: 20
    params:
        ref_fasta=config["reference_fasta"],
    shell:
        """cuddlySV {input.cram} {params.ref_fasta} {output.joint_vcf} {input.workdir}/ \
    --threads {threads} --report_readid --sample {wildcards.tumor} --min_support 3 --genotype \
    --max_cluster_bias_INS 100 --diff_ratio_merging_INS 0.3 --min_size 10 --max_cluster_bias_DEL 100 \
    --diff_ratio_merging_DEL 0.3 --retain_work_dir --verbose --max_size -1 --report_readgroup 2>{log};
    """

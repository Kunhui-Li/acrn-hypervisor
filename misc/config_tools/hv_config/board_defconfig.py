# Copyright (C) 2019 Intel Corporation. All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
#

import sys
import subprocess # nosec
import board_cfg_lib, scenario_cfg_lib
import hv_cfg_lib
import common


DESC = """# Board defconfig generated by acrn-config tool
"""

MEM_ALIGN = 2 * common.SIZE_M


def get_serial_type():
    """ Get serial console type specified by user """
    ttys_type = ''
    ttys_value = ''
    pci_mmio = False

    # Get ttySx information from board config file
    ttys_lines = board_cfg_lib.get_info(common.BOARD_INFO_FILE, "<TTYS_INFO>", "</TTYS_INFO>")

    # Get ttySx from scenario config file which selected by user
    (err_dic, ttyn) = board_cfg_lib.parser_hv_console()
    if err_dic:
        hv_cfg_lib.ERR_LIST.update(err_dic)

    # query the serial type from board config file
    for line in ttys_lines:
        if ttyn in line:
            # line format:
            # seri:/dev/ttyS0 type:portio base:0x3F8 irq:4
            # seri:/dev/ttyS0 type:mmio base:0xB3640000 irq:4 [bdf:"0:x.y"]
            ttys_type = line.split()[1].split(':')[1]
            if ttys_type == "portio":
                ttys_value = line.split()[2].split(':')[1]
            elif ttys_type == "mmio":
                if 'bdf' in line:
                    ttys_value = line.split()[-1].split('"')[1:-1][0]
                    pci_mmio = True
                else:
                    ttys_value = line.split()[2].split(':')[1]
            break

    return (ttys_type, ttys_value, pci_mmio)


def get_memory(hv_info, config):

    # We recommend to put hv ram start address high than 0x10000000 to
    # reduce memory conflict with GRUB/Service VM Kernel.
    hv_start_offset = 0x10000000
    for start_addr in list(board_cfg_lib.USED_RAM_RANGE):
        if hv_start_offset <= start_addr < 0x80000000:
            del board_cfg_lib.USED_RAM_RANGE[start_addr]

    print("CONFIG_HV_RAM_START={}".format(hv_info.mem.hv_ram_start), file=config)
    print("CONFIG_STACK_SIZE={}".format(hv_info.mem.stack_size), file=config)
    print("CONFIG_IVSHMEM_ENABLED={}".format(hv_info.mem.ivshmem_enable), file=config)


def get_serial_console(config):

    (serial_type, serial_value, pci_mmio) = get_serial_type()
    if serial_type == "portio":
        print("CONFIG_SERIAL_LEGACY=y", file=config)
        print("CONFIG_SERIAL_PIO_BASE={}".format(serial_value), file=config)
    elif serial_type == "mmio" and pci_mmio:
        print("CONFIG_SERIAL_PCI=y", file=config)
        if serial_value:
            bus = int(serial_value.strip("'").split(':')[0], 16)
            dev = int(serial_value.strip("'").split(':')[1].split(".")[0], 16)
            fun = int(serial_value.strip("'").split('.')[1], 16)
            value = ((bus & 0xFF) << 8) | ((dev & 0x1F) << 3) | (fun & 0x7)
            print('CONFIG_SERIAL_PCI_BDF={}'.format(hex(value)), file=config)
    else:
        print("CONFIG_SERIAL_MMIO=y", file=config)
        if serial_value:
            print('CONFIG_SERIAL_MMIO_BASE={}'.format(serial_value), file=config)

def get_features(hv_info, config):

    print("CONFIG_{}=y".format(hv_info.features.scheduler), file=config)
    print("CONFIG_RELOC={}".format(hv_info.features.reloc), file=config)
    print("CONFIG_MULTIBOOT2={}".format(hv_info.features.multiboot2), file=config)
    print("CONFIG_RDT_ENABLED={}".format(hv_info.features.rdt_enabled), file=config)
    if hv_info.features.rdt_enabled == 'y':
        print("CONFIG_CDP_ENABLED={}".format(hv_info.features.cdp_enabled), file=config)
    else:
        print("CONFIG_CDP_ENABLED=n", file=config)
    print("CONFIG_HYPERV_ENABLED={}".format(hv_info.features.hyperv_enabled), file=config)
    print("CONFIG_IOMMU_ENFORCE_SNP={}".format(hv_info.features.iommu_enforce_snp), file=config)
    print("CONFIG_ACPI_PARSE_ENABLED={}".format(hv_info.features.acpi_parse_enabled), file=config)
    print("CONFIG_L1D_FLUSH_VMENTRY_ENABLED={}".format(hv_info.features.l1d_flush_vmentry_enabled), file=config)
    print("CONFIG_MCE_ON_PSC_WORKAROUND_DISABLED={}".format(hv_info.features.mce_on_psc_workaround_disabled), file=config)
    if hv_info.features.ssram_enabled in ['y', 'n']:
        print("CONFIG_SSRAM_ENABLED={}".format(hv_info.features.ssram_enabled), file=config)


def get_capacities(hv_info, config):

    print("CONFIG_IOMMU_BUS_NUM={}".format(hv_info.cap.iommu_bus_num), file=config)
    print("CONFIG_MAX_IOAPIC_NUM={}".format(hv_info.cap.max_ioapic_num), file=config)
    print("CONFIG_MAX_PCI_DEV_NUM={}".format(hv_info.cap.max_pci_dev_num), file=config)
    print("CONFIG_MAX_IOAPIC_LINES={}".format(hv_info.cap.max_ioapic_lines), file=config)
    print("CONFIG_MAX_PT_IRQ_ENTRIES={}".format(hv_info.cap.max_pt_irq_entries), file=config)
    max_msix_table_num = 0
    if not hv_info.cap.max_msix_table_num:
        native_max_msix_line = board_cfg_lib.get_info(common.BOARD_INFO_FILE, "<MAX_MSIX_TABLE_NUM>", "</MAX_MSIX_TABLE_NUM>")
        max_msix_table_num = native_max_msix_line[0].strip()
    else:
        max_msix_table_num = hv_info.cap.max_msix_table_num
    print("CONFIG_MAX_MSIX_TABLE_NUM={}".format(max_msix_table_num), file=config)
    print("CONFIG_MAX_EMULATED_MMIO_REGIONS={}".format(hv_info.cap.max_emu_mmio_regions), file=config)


def get_log_opt(hv_info, config):

    print("CONFIG_NPK_LOGLEVEL_DEFAULT={}".format(hv_info.log.level.npk), file=config)
    print("CONFIG_MEM_LOGLEVEL_DEFAULT={}".format(hv_info.log.level.mem), file=config)
    print("CONFIG_CONSOLE_LOGLEVEL_DEFAULT={}".format(hv_info.log.level.console), file=config)


def generate_file(hv_info, config):
    """Start to generate board.c
    :param config: it is a file pointer of board information for writing to
    """
    err_dic = {}

    # add config scenario name
    (err_dic, scenario_name) = common.get_scenario_name()
    (err_dic, board_name) = common.get_board_name()

    print("{}".format(DESC), file=config)
    if hv_info.log.release == 'y':
        print("CONFIG_RELEASE=y", file=config)
    print('CONFIG_BOARD="{}"'.format(board_name), file=config)

    get_memory(hv_info, config)
    get_features(hv_info, config)
    get_capacities(hv_info, config)
    get_serial_console(config)
    get_log_opt(hv_info, config)

    return err_dic

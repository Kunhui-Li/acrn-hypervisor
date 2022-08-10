#!/usr/bin/env python3
#
# Copyright (C) 2022 Intel Corporation.
#
# SPDX-License-Identifier: BSD-3-Clause
#

import re

import os
import sys

import copy
import argparse

import logging

import lxml.etree as etree


def eval_xpath(element, xpath, default_value=None):
    return next(iter(element.xpath(xpath)), default_value)


def eval_xpath_all(element, xpath):
    return element.xpath(xpath)


class LaunchScript:
    script_template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launch_script_template.sh")

    class VirtualBDFAllocator:
        def __init__(self):
            # Reserved slots:
            #    0 - For (virtual) hostbridge
            #    1 - For (virtual) LPC
            #    2 - For passthrough integarted GPU (either PF or VF)
            #   31 - For LPC bridge needed by integrated GPU
            self._free_slots = list(range(3, 30))

        def get_virtual_bdf(self, device_etree=None, options=None):
            if device_etree is not None:
                bus = eval_xpath(device_etree, "../@address")
                vendor_id = eval_xpath(device_etree, "vendor/text()")
                class_code = eval_xpath(device_etree, "class/text()")

                # VGA-compatible controller, either integrated or discrete GPU
                if class_code == "0x030000":
                    return 2

            if options:
                if "igd" in options:
                    return 2

            next_vbdf = self._free_slots.pop(0)
            return next_vbdf

        def remove_virtual_bdf(self, slot):
            if slot in self._free_slots:
                self._free_slots.remove(slot)

    class PassThruDeviceOptions:
        passthru_device_options = {
            "0x0200": [".//PTM[text()='y']", "enable_ptm"],  # Ethernet controller, added if PTM is enabled for the VM
            "0x0c0330": [".//os_type[text()='Windows OS']", "d3hot_reset"],
        }

        def __init__(self, vm_scenario_etree):
            self._options = copy.copy(self.passthru_device_options)

        def get_option(self, device_etree, vm_scenario_etree):
            passthru_options = []
            if device_etree is not None:
                class_code = eval_xpath(device_etree, "class/text()", "")
                for k, v in self._options.items():
                    if class_code.startswith(k) and vm_scenario_etree.xpath(v[0]):
                        passthru_options.extend(v[1:])
            return ",".join(passthru_options)

    def __init__(self, board_etree, vm_name, vm_scenario_etree):
        self._board_etree = board_etree
        self._vm_scenario_etree = vm_scenario_etree

        self._vm_name = vm_name
        self._vm_descriptors = {}
        self._init_commands = []
        self._cpu_dict = {}
        self._dm_parameters = []
        self._deinit_commands = []

        self._vbdf_allocator = self.VirtualBDFAllocator()
        self._passthru_options = self.PassThruDeviceOptions(vm_scenario_etree)

    def add_vm_descriptor(self, name, value):
        self._vm_descriptors[name] = value

    def add_sos_cpu_dict(self, cpu_and_lapic_id_list):
        self._cpu_dict = dict(cpu_and_lapic_id_list)

    def add_init_command(self, command):
        if command not in self._init_commands:
            self._init_commands.append(command)

    def add_deinit_command(self, command):
        if command not in self._deinit_commands:
            self._deinit_commands.append(command)

    def add_plain_dm_parameter(self, opt):
        full_opt = f"{opt}"
        if full_opt not in self._dm_parameters:
            self._dm_parameters.append(full_opt)

    def add_dynamic_dm_parameter(self, cmd, opt=""):
        full_cmd = f"{cmd:40s} {opt}".strip()
        full_opt = f"`{full_cmd}`"
        if full_opt not in self._dm_parameters:
            self._dm_parameters.append(full_opt)

    def to_string(self):
        s = ""

        with open(self.script_template_path, "r") as f:
            s += f.read(99)

        s += "# Launch script for VM name: "
        s += f"{self._vm_name}\n"
        s += "\n"

        with open(self.script_template_path, "r") as f:
            f.seek(99,0)
            s += f.read()

        s += """
###
# The followings are generated by launch_cfg_gen.py
###
"""
        s += "\n"

        s += "# Defining variables that describe VM types\n"
        for name, value in self._vm_descriptors.items():
            s += f"{name}={value}\n"
        s += "\n"

        s += "# Initializing\n"
        for command in self._init_commands:
            s += f"{command}\n"
        s += "\n"

        s += """
# Note for developers: The number of available logical CPUs depends on the
# number of enabled cores and whether Hyperthreading is enabled in the BIOS
# settings. CPU IDs are assigned to each logical CPU but are not the same ID
# value throughout the system:
#
# Native CPU_ID:
#       ID enumerated by the Linux Kernel and shown in the
#       ACRN Configurator's CPU Affinity option (used in the scenario.xml)
# Service VM CPU_ID:
#       ID assigned by the Service VM at runtime
# APIC_ID:
#       Advanced Programmable Interrupt Controller's unique ID as
#       enumerated by the board inspector (used in this launch script)
#
# This table shows equivalent CPU IDs for this scenario and board:
#
"""
        s += "\n"

        s += "#   Native CPU_ID    Service VM CPU_ID    APIC_ID\n"
        s += "#   -------------    -----------------    -------\n"
        vcpu_id = 0
        for cpu_info in self._cpu_dict:
            s += "#   "
            s += f"{cpu_info:3d}{'':17s}"
            s += f"{vcpu_id:3d}{'':17s}"
            s += f"{self._cpu_dict[cpu_info]:3d}\n"
            vcpu_id += 1
        s += "\n"
        s += "# Invoking ACRN device model\n"
        s += "dm_params=(\n"
        for param in self._dm_parameters:
            s += f"    {param}\n"
        s += ")\n\n"

        s += "echo \"Launch device model with parameters: ${dm_params[@]}\"\n"
        s += "acrn-dm \"${dm_params[@]}\"\n\n"

        s += "# Deinitializing\n"
        for command in self._deinit_commands:
            s += f"{command}\n"

        return s

    def write_to_file(self, path):
        with open(path, "w") as f:
            f.write(self.to_string())
            logging.info(f"Successfully generated launch script {path} for VM '{self._vm_name}'.")

    def add_virtual_device(self, kind, vbdf=None, options=""):
        if "virtio" in kind and eval_xpath(self._vm_scenario_etree, ".//vm_type/text()") == "RTVM":
            self.add_plain_dm_parameter("--virtio_poll 1000000")

        if vbdf is None:
            vbdf = self._vbdf_allocator.get_virtual_bdf()
        else:
            self._vbdf_allocator.remove_virtual_bdf(vbdf)
        self.add_dynamic_dm_parameter("add_virtual_device", f"{vbdf} {kind} {options}")

    def add_passthru_device(self, bus, dev, fun, options=""):
        device_etree = eval_xpath(
            self._board_etree,
            f"//bus[@type='pci' and @address='0x{bus:x}']/device[@address='0x{(dev << 16) | fun:x}']"
        )
        if not options:
            options = self._passthru_options.get_option(device_etree, self._vm_scenario_etree)

        vbdf = self._vbdf_allocator.get_virtual_bdf(device_etree, options)
        self.add_dynamic_dm_parameter("add_passthrough_device", f"{vbdf} 0000:{bus:02x}:{dev:02x}.{fun} {options}")

        # Enable interrupt storm monitoring if the VM has any passthrough device other than the integrated GPU (whose
        # vBDF is fixed to 2)
        if vbdf != 2:
            self.add_dynamic_dm_parameter("add_interrupt_storm_monitor", "10000 10 1 100")

    def has_dm_parameter(self, fn):
        try:
            next(filter(fn, self._dm_parameters))
            return True
        except StopIteration:
            return False


def cpu_id_to_lapic_id(board_etree, vm_name, cpu):

    lapic_id = eval_xpath(board_etree, f"//processors//thread[cpu_id='{cpu}']/apic_id/text()", None)
    if lapic_id is not None:
        return int(lapic_id, 16)
    else:
        logging.warning(f"CPU {cpu} is not defined in the board XML, so it can't be available to VM {vm_name}")
        return None

def get_slot_by_vbdf(vbdf):
    if vbdf is not None:
        return int((vbdf.split(":")[1].split(".")[0]), 16)
    else:
        return None

def generate_for_one_vm(board_etree, hv_scenario_etree, vm_scenario_etree, vm_id):
    vm_name = eval_xpath(vm_scenario_etree, "./name/text()", f"ACRN Post-Launched VM")
    script = LaunchScript(board_etree, vm_name, vm_scenario_etree)

    script.add_init_command("probe_modules")

    ###
    # VM types and guest OSes
    ###

    if eval_xpath(vm_scenario_etree, ".//os_type/text()") == "Windows OS":
        script.add_plain_dm_parameter("--windows")
    script.add_vm_descriptor("vm_type", f"'{eval_xpath(vm_scenario_etree, './/vm_type/text()', 'STANDARD_VM')}'")
    script.add_vm_descriptor("scheduler", f"'{eval_xpath(hv_scenario_etree, './/SCHEDULER/text()')}'")

    ###
    # CPU and memory resources
    ###
    cpus = set(eval_xpath_all(vm_scenario_etree, ".//cpu_affinity//pcpu_id[text() != '']/text()"))
    lapic_ids = [x for x in [cpu_id_to_lapic_id(board_etree, vm_name, cpu_id) for cpu_id in cpus] if x != None]
    if lapic_ids:
        script.add_dynamic_dm_parameter("add_cpus", f"{' '.join([str(x) for x in sorted(lapic_ids)])}")

    script.add_plain_dm_parameter(f"-m {eval_xpath(vm_scenario_etree, './/memory/size/text()')}M")

    if eval_xpath(vm_scenario_etree, "//SSRAM_ENABLED") == "y" and \
            eval_xpath(vm_scenario_etree, ".//vm_type/text()") == "RTVM":
        script.add_plain_dm_parameter("--ssram")

    ###
    # Guest BIOS
    ###
    if eval_xpath(vm_scenario_etree, ".//vbootloader/text()") == "y":
        script.add_plain_dm_parameter("--ovmf /usr/share/acrn/bios/OVMF.fd")

    ###
    # Devices
    ###

    # Emulated platform devices
    if eval_xpath(vm_scenario_etree, ".//vm_type/text()") != "RTVM":
        script.add_virtual_device("lpc", vbdf="1:0")

    if eval_xpath(vm_scenario_etree, ".//vuart0/text()") == "y":
        script.add_plain_dm_parameter("-l com1,stdio")

    # Emulated PCI devices
    script.add_virtual_device("hostbridge", vbdf="0:0")

    #ivshmem and vuart must be the first virtual devices generated before the others except hostbridge and LPC
    #ivshmem and vuart own reserved slots which setting by user

    for ivshmem in eval_xpath_all(vm_scenario_etree, f"//IVSHMEM_REGION[PROVIDED_BY = 'Device Model' and .//VM_NAME = '{vm_name}']"):
        vbdf = eval_xpath(ivshmem, f".//VBDF/text()")
        slot = get_slot_by_vbdf(vbdf)
        script.add_virtual_device("ivshmem", slot, options=f"dm:/{ivshmem.find('NAME').text},{ivshmem.find('IVSHMEM_SIZE').text}")

    for ivshmem in eval_xpath_all(vm_scenario_etree, f"//IVSHMEM_REGION[PROVIDED_BY = 'Hypervisor' and .//VM_NAME = '{vm_name}']"):
        vbdf = eval_xpath(ivshmem, f".//VBDF/text()")
        slot = get_slot_by_vbdf(vbdf)
        script.add_virtual_device("ivshmem", slot, options=f"hv:/{ivshmem.find('NAME').text},{ivshmem.find('IVSHMEM_SIZE').text}")

    if eval_xpath(vm_scenario_etree, ".//console_vuart/text()") == "PCI":
        script.add_virtual_device("uart", options="vuart_idx:0")

    for idx, conn in enumerate(eval_xpath_all(hv_scenario_etree, f"//vuart_connection[endpoint/vm_name/text() = '{vm_name}']"), start=1):
        if eval_xpath(conn, f"./type/text()") == "pci":
            vbdf = eval_xpath(conn, f"./endpoint[vm_name/text() = '{vm_name}']/vbdf/text()")
            slot = get_slot_by_vbdf(vbdf)
            script.add_virtual_device("uart", slot, options=f"vuart_idx:{idx}")

    # Mediated PCI devices, including virtio
    for usb_xhci in eval_xpath_all(vm_scenario_etree, ".//usb_xhci/usb_dev[text() != '']/text()"):
        bus_port = usb_xhci.split(' ')[0]
        script.add_virtual_device("xhci", options=bus_port)

    for virtio_input_etree in eval_xpath_all(vm_scenario_etree, ".//virtio_devices/input"):
        backend_device_file = eval_xpath(virtio_input_etree, "./backend_device_file[text() != '']/text()")
        unique_identifier = eval_xpath(virtio_input_etree, "./id[text() != '']/text()")
        if backend_device_file is not None and unique_identifier is not None:
            script.add_virtual_device("virtio-input", options=f"{backend_device_file},id:{unique_identifier}")
        elif backend_device_file is not None:
            script.add_virtual_device("virtio-input", options=backend_device_file)

    for virtio_console_etree in eval_xpath_all(vm_scenario_etree, ".//virtio_devices/console"):
        preceding_mask = ""
        use_type = eval_xpath(virtio_console_etree, "./use_type/text()")
        backend_type = eval_xpath(virtio_console_etree, "./backend_type/text()")
        if use_type == "Virtio console":
            preceding_mask = "@"

        if backend_type == "file":
            output_file_path = eval_xpath(virtio_console_etree, "./output_file_path/text()")
            script.add_virtual_device("virtio-console", options=f"{preceding_mask}file:file_port={output_file_path}")
        elif backend_type == "tty":
            tty_file_path = eval_xpath(virtio_console_etree, "./tty_device_path/text()")
            script.add_virtual_device("virtio-console", options=f"{preceding_mask}tty:tty_port={tty_file_path}")
        elif backend_type == "sock server" or backend_type == "sock client":
            sock_file_path = eval_xpath(virtio_console_etree, "./sock_file_path/text()")
            script.add_virtual_device("virtio-console", options=f"socket:{os.path.basename(sock_file_path).split('.')[0]}={sock_file_path}:{backend_type.replace('sock ', '')}")
        elif backend_type == "pty" or backend_type == "stdio":
            script.add_virtual_device("virtio-console", options=f"{preceding_mask}{backend_type}:{backend_type}_port")

    for virtio_network_etree in eval_xpath_all(vm_scenario_etree, ".//virtio_devices/network"):
        virtio_framework = eval_xpath(virtio_network_etree, "./virtio_framework/text()")
        interface_name = eval_xpath(virtio_network_etree, "./interface_name/text()")
        params = interface_name.split(",", maxsplit=1)
        tap_conf = f"tap={params[0]}"
        params = [tap_conf] + params[1:]
        if virtio_framework == "Kernel based (Virtual Host)":
            params.append("vhost")
        script.add_init_command(f"mac=$(cat /sys/class/net/e*/address)")
        params.append(f"mac_seed=${{mac:0:17}}-{vm_name}")
        script.add_virtual_device("virtio-net", options=",".join(params))

    for virtio_block in eval_xpath_all(vm_scenario_etree, ".//virtio_devices/block[text() != '']/text()"):
        params = virtio_block.split(":", maxsplit=1)
        if len(params) == 1:
            script.add_virtual_device("virtio-blk", options=virtio_block)
        else:
            block_device = params[0]
            rootfs_img = params[1]
            var = f"dir_{os.path.basename(block_device)}"
            script.add_init_command(f"{var}=`mount_partition {block_device}`")
            script.add_virtual_device("virtio-blk", options=os.path.join(f"${{{var}}}", rootfs_img))
            script.add_deinit_command(f"unmount_partition ${{{var}}}")

    for gpu_etree in eval_xpath_all(vm_scenario_etree, ".//virtio_devices/gpu"):
        display_type = eval_xpath(gpu_etree, "./display_type[text() != '']/text()")
        params = list()
        for display_etree in eval_xpath_all(gpu_etree, "./displays/display"):
            if display_type == "Window":
                window_dimensions = eval_xpath(display_etree, "./window_dimensions/text()")
                horizontal_offset = eval_xpath(display_etree, "./horizontal_offset/text()")
                vertical_offset = eval_xpath(display_etree, "./vertical_offset/text()")
                params.append(f"geometry={window_dimensions}+{horizontal_offset}+{vertical_offset}")
            if display_type == "Full screen":
                monitor_id = eval_xpath(display_etree, "./monitor_id/text()")
                params.append(f"geometry=fullscreen:{monitor_id}")
        script.add_virtual_device("virtio-gpu", options=",".join(params))

    for vsock in eval_xpath_all(vm_scenario_etree, ".//virtio_devices/vsock[text() != '']/text()"):
        script.add_virtual_device("vhost-vsock", options="cid="+vsock)

    # Passthrough PCI devices
    bdf_regex = re.compile("([0-9a-f]{2}):([0-1][0-9a-f]).([0-7])")
    for passthru_device in eval_xpath_all(vm_scenario_etree, ".//pci_devs/*/text()"):
        m = bdf_regex.match(passthru_device)
        if not m:
            continue
        bus = int(m.group(1), 16)
        dev = int(m.group(2), 16)
        func = int(m.group(3), 16)
        script.add_passthru_device(bus, dev, func)

    ###
    # Miscellaneous
    ###
    if eval_xpath(vm_scenario_etree, ".//vm_type/text()") == "RTVM":
        script.add_plain_dm_parameter("--rtvm")
        if eval_xpath(vm_scenario_etree, ".//lapic_passthrough/text()") == "y":
            script.add_plain_dm_parameter("--lapic_pt")
    script.add_dynamic_dm_parameter("add_logger_settings", "console=4 kmsg=3 disk=5")

    ###
    # Lastly, conclude the device model parameters with the VM name
    ###
    script.add_plain_dm_parameter(f"{vm_name}")

    return script


def main(board_xml, scenario_xml, user_vm_id, out_dir):
    board_etree = etree.parse(board_xml)
    scenario_etree = etree.parse(scenario_xml)

    service_vm_id = eval_xpath(scenario_etree, "//vm[load_order = 'SERVICE_VM']/@id")
    service_vm_name = eval_xpath(scenario_etree, "//vm[load_order = 'SERVICE_VM']/name/text()")

    hv_scenario_etree = eval_xpath(scenario_etree, "//hv")
    post_vms = eval_xpath_all(scenario_etree, "//vm[load_order = 'POST_LAUNCHED_VM']")
    if service_vm_id is None and len(post_vms) > 0:
        logging.error("The scenario does not define a service VM so no launch scripts will be generated for the post-launched VMs in the scenario.")
        return 1
    service_vm_id = int(service_vm_id)

    # Service VM CPU list
    pre_all_cpus = eval_xpath_all(scenario_etree, "//vm[load_order = 'PRE_LAUNCHED_VM']/cpu_affinity//pcpu_id/text()")
    cpus_for_sos = sorted([int(x) for x in eval_xpath_all(board_etree, "//processors//thread//cpu_id/text()") if x not in pre_all_cpus])

    try:
        os.mkdir(out_dir)
    except FileExistsError:
        if os.path.isfile(out_dir):
            logging.error(f"Cannot create output directory {out_dir}: File exists")
            return 1
    except Exception as e:
        logging.error(f"Cannot create output directory: {e}")
        return 1

    if user_vm_id == 0:
        post_vm_ids = [int(vm_scenario_etree.get("id")) for vm_scenario_etree in post_vms]
    else:
        post_vm_ids = [user_vm_id + service_vm_id]

    for post_vm in post_vms:
        post_vm_id = int(post_vm.get("id"))
        if post_vm_id not in post_vm_ids:
            continue

        script = generate_for_one_vm(board_etree, hv_scenario_etree, post_vm, post_vm_id)
        script.add_sos_cpu_dict([(x, cpu_id_to_lapic_id(board_etree, service_vm_name, x)) for x in cpus_for_sos])
        script.write_to_file(os.path.join(out_dir, f"launch_user_vm_id{post_vm_id - service_vm_id}.sh"))

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", help="the XML file summarizing characteristics of the target board")
    parser.add_argument("--scenario", help="the XML file specifying the scenario to be set up")
    parser.add_argument("--launch", default=None, help="(obsoleted. DO NOT USE)")
    parser.add_argument("--user_vmid", type=int, default=0,
                        help="the post-launched VM ID (as is specified in the launch XML) whose launch script is to be generated, or 0 if all post-launched VMs shall be processed")
    parser.add_argument("--out", default="output", help="path to the directory where generated scripts are placed")
    args = parser.parse_args()

    logging.basicConfig(level="INFO")

    sys.exit(main(args.board, args.scenario, args.user_vmid, args.out))

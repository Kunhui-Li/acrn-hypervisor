/*
 * Copyright (C) 2021 Intel Corporation.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */

#ifndef VM_CONFIGURATIONS_H
#define VM_CONFIGURATIONS_H

#include <misc_cfg.h>
#include <pci_devices.h>
/* SERVICE_VM_NUM can only be 0 or 1; When SERVICE_VM_NUM is 1, MAX_POST_VM_NUM must be 0 too. */
#define PRE_VM_NUM 2U
#define SERVICE_VM_NUM 0U
#define MAX_POST_VM_NUM 14U
#define CONFIG_MAX_VM_NUM 16U
#define VM0_CONFIG_MEM_START_HPA 0x100000000UL
#define VM0_CONFIG_MEM_SIZE 0x20000000UL
#define VM0_CONFIG_MEM_START_HPA2 0x0UL
#define VM0_CONFIG_MEM_SIZE_HPA2 0x0UL
#define VM1_CONFIG_MEM_START_HPA 0x120000000UL
#define VM1_CONFIG_MEM_SIZE 0x20000000UL
#define VM1_CONFIG_MEM_START_HPA2 0x0UL
#define VM1_CONFIG_MEM_SIZE_HPA2 0x0UL

#endif /* VM_CONFIGURATIONS_H */

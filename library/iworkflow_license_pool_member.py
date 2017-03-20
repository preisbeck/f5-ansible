#!/usr/bin/python
#
# Copyright 2016 F5 Networks Inc.
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

ANSIBLE_METADATA = {
    'status': ['preview'],
    'supported_by': 'community',
    'metadata_version': '1.0'
}

DOCUMENTATION = '''
module: iworkflow_license_pool_member
short_description: Manages members in a license pool.
description:
  - Manages members in a license pool. By adding and removing members from
    a pool, you will implicitly be licensing and unlicensing them.
version_added: 2.3
options:
  member:
    description:
      - Name of the managed device to add to the license pool.
    required: True
  pool:
    description:
      - The license pool that you want to add the member to.
  state:
    description:
      - Whether the member should exist in the pool (and therefore be licensed)
        or if it should not (and therefore be unlicensed).
    required: false
    default: present
    choices:
      - present
      - absent
notes:
  - Requires the f5-sdk Python package on the host. This is as easy as pip
    install f5-sdk.
extends_documentation_fragment: f5
requirements:
    - f5-sdk >= 2.2.0
    - iWorkflow >= 2.1.0
author:
    - Tim Rupp (@caphrim007)
'''

EXAMPLES = '''

'''

RETURN = '''

'''

import time

from ansible.module_utils.f5_utils import *


class Parameters(AnsibleF5Parameters):
    returnables = []
    api_attributes = []

    def to_return(self):
        result = {}
        for returnable in self.returnables:
            result[returnable] = getattr(self, returnable)
        result = self._filter_params(result)
        return result

    def api_params(self):
        result = {}
        for api_attribute in self.api_attributes:
            result[api_attribute] = getattr(self, api_attribute)
        result = self._filter_params(result)
        return result

    @property
    def devices(self):
        return self._values['devices']

    @devices.setter
    def devices(self, value):
        needs_device_references = False
        if isinstance(value, basestring):
            collection = self._get_device_collection()
            self._values['devices'] = self._get_device_selflinks([str(value)], collection)
        else:
            result = []

            for item in value:
                try:
                    # Case for the REST API
                    member = dict(
                        deviceReference=str(item['deviceReference']['link']),
                        hostname=str(item['deviceReference']['hostname']),
                        address=str(item['deviceReference']['address']),
                        managementAddress=str(item['deviceReference']['managementAddress']),
                        selfLink=str(item['selfLink'])
                    )
                except KeyError:
                    needs_device_references = True
                    member = item
                result.append(member)

            if needs_device_references:
                collection = self._get_device_collection()
                self._values['devices'] = self._get_device_selflinks(result, collection)
            else:
                self._values['devices'] = result

    def _get_device_selflinks(self, devices, collection):
        result = []
        resource = None
        for device in collection:
            if str(device.product) != "BIG-IP":
                continue
            # The supplied device can be in several formats.
            if str(device.hostname) in devices:
                resource = device
                break
            elif str(device.address) in devices:
                resource = device
                break
            elif str(device.managementAddress) in devices:
                resource = device
                break
        if not resource:
            raise F5ModuleError(
                "Device {0} was not found".format(devices)
            )
        result.append(resource.selfLink)
        return result

    def _get_device_collection(self):
        dg = self.client.api.shared.resolver.device_groups
        return dg.cm_cloud_managed_devices.devices_s.get_collection()


class ModuleManager(object):
    def __init__(self, client):
        self.client = client
        self.have = None
        self.want = Parameters(self.client.module.params)
        self.changes = Parameters()

    def _load_pool_by_name(self):
        collection = self.client.api.cm.shared.licensing.pools_s.get_collection(
            requests_params=dict(
                params="$filter=name+eq+'{0}'".format(self.want.pool)
            )
        )
        if len(collection) == 1:
            return collection[0]
        elif len(collection) == 0:
            raise F5ModuleError(
                "The specified pool was not found"
            )
        else:
            raise F5ModuleError(
                "Multiple pools with the provided name were found!"
            )

    def _wait_for_pool_member_state_to_license(self, member):
        error_values = ['FAILED']
        # Wait no more than half an hour
        for x in range(1, 180):
            member.refresh()
            if member.state == 'LICENSED':
                break
            elif member.state in error_values:
                raise F5ModuleError(member.errors)
            time.sleep(10)

    def exec_module(self):
        changed = False
        result = dict()
        state = self.want.state

        try:
            if state == "present":
                changed = self.present()
            elif state == "absent":
                changed = self.absent()
        except IOError as e:
            raise F5ModuleError(str(e))

        changes = self.changes.to_return()
        result.update(**changes)
        result.update(dict(changed=changed))
        return result

    def exists(self):
        if not self.have.devices:
            return False
        have_members = set([x.selfLink for x in self.have.devices])
        want_members = set([x.selfLink for x in self.want.devices])
        if want_members.issubset(have_members):
            return True
        return False

    def present(self):
        self.have = self.read_current_from_device()
        if self.exists():
            return False
        else:
            return self.create()

    def create(self):
        if self.client.check_mode:
            return True
        self.create_on_device()
        return True

    def create_on_device(self):
        device_refs = self.to_add()
        pool = self._load_pool_by_name()
        if not pool:
            raise F5ModuleError(
                "Pool disappeared during member licensing."
            )
        for member in device_refs:
            pool.members_s.member.create(
                deviceReference=dict(
                    link=member
                )
            )
            self._wait_for_pool_member_state_to_license(member)
        return True

    def read_current_from_device(self):
        resource = self._load_pool_by_name()
        collection = resource.members_s.get_collection(
            params='$expand=deviceReference'
        )
        result = Parameters(dict(
            pool=self.want.pool,
            devices=collection
        ))
        return result

    def absent(self):
        if self.exists():
            return self.remove()
        return False

    def remove(self):
        if self.client.check_mode:
            return True
        self.remove_from_device()
        if self.exists():
            raise F5ModuleError("Failed to remove the pool member")
        return True

    def remove_from_device(self):
        references = self.to_remove()
        collection = self._load_pool_by_name()
        for member in collection.members_s.get_collection():
            if member.selfLink in references:
                member.delete()
        return True

    def to_add(self):
        want = set(self.want.devices)
        have = set([x['deviceReference'] for x in self.have.devices])
        return set(want - have)

    def to_remove(self):
        want = set([x['selfLink'] for x in self.want.devices])
        have = set([x['deviceReference'] for x in self.want.member])
        references = set(have - want)

        # The code that deletes things doesn't know anything about devices,
        # so you need to supply the member selfLink when doing comparisons
        # for deletion
        return [x['selfLink'] for x in self.want.devices
                if x['deviceReference'] in references]


class ArgumentSpec(object):
    def __init__(self):
        self.supports_check_mode = True
        self.argument_spec = dict(
            pool=dict(
                required=True
            ),
            devices=dict(
                type='str',
                required=True,
                aliases=['device']
            ),
            state=dict(
                required=False,
                default='present',
                choices=['absent', 'present']
            )
        )
        self.f5_product_name = 'iworkflow'


def main():
    if not HAS_F5SDK:
        raise F5ModuleError("The python f5-sdk module is required")

    spec = ArgumentSpec()

    client = AnsibleF5Client(
        argument_spec=spec.argument_spec,
        supports_check_mode=spec.supports_check_mode,
        f5_product_name=spec.f5_product_name
    )

    try:
        mm = ModuleManager(client)
        results = mm.exec_module()
        client.module.exit_json(**results)
    except F5ModuleError as e:
        client.module.fail_json(msg=str(e))

if __name__ == '__main__':
    main()

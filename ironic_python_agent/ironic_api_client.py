"""
Copyright 2013 Rackspace, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import json
import requests

from ironic_python_agent import encoding
from ironic_python_agent import errors
from ironic_python_agent.openstack.common import log
from ironic_python_agent.openstack.common import loopingcall


class APIClient(object):
    api_version = 'v1'

    def __init__(self, api_url):
        self.api_url = api_url.rstrip('/')
        self.session = requests.Session()
        self.encoder = encoding.RESTJSONEncoder()
        self.log = log.getLogger(__name__)

    def _request(self, method, path, data=None):
        request_url = '{api_url}{path}'.format(api_url=self.api_url, path=path)

        if data is not None:
            data = self.encoder.encode(data)

        request_headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

        return self.session.request(method,
                                    request_url,
                                    headers=request_headers,
                                    data=data)

    def heartbeat(self, uuid, advertise_address):
        path = '/{api_version}/nodes/{uuid}/vendor_passthru/heartbeat'.format(
            api_version=self.api_version,
            uuid=uuid
        )
        data = {
            'agent_url': self._get_agent_url(advertise_address)
        }
        try:
            response = self._request('POST', path, data=data)
        except Exception as e:
            raise errors.HeartbeatError(str(e))

        if response.status_code != requests.codes.NO_CONTENT:
            msg = 'Invalid status code: {0}'.format(response.status_code)
            raise errors.HeartbeatError(msg)

        try:
            return float(response.headers['Heartbeat-Before'])
        except KeyError:
            raise errors.HeartbeatError('Missing Heartbeat-Before header')
        except Exception:
            raise errors.HeartbeatError('Invalid Heartbeat-Before header')

    def lookup_node(self, hardware_info, timeout, starting_interval):
        timer = loopingcall.DynamicLoopingCall(
            self._do_lookup,
            hardware_info=hardware_info,
            intervals=[starting_interval],
            total_time=[0],
            timeout=timeout)
        node_content = timer.start().wait()

        # True is returned on timeout
        if node_content is True:
            raise errors.LookupNodeError('Could not look up node info. Check '
                                         'logs for details.')
        return node_content

    def _do_lookup(self, hardware_info, timeout, intervals=[1],
                   total_time=[0]):
        """The actual call to lookup a node. Should be called inside
        loopingcall.DynamicLoopingCall.

        intervals and total_time are mutable so it can be changed by each run
        in the looping call and accessed/changed on the next run.
        """
        def next_interval(timeout, intervals=[], total_time=[]):
            """Function to calculate what the next interval should be. Uses
            exponential backoff and raises an exception (that won't
            be caught by do_lookup) to kill the looping call if it goes too
            long
            """
            new_interval = intervals[-1] * 2
            if total_time[0] + new_interval > timeout:
                # No retvalue signifies error
                raise loopingcall.LoopingCallDone()

            total_time[0] += new_interval
            intervals.append(new_interval)
            return new_interval

        path = '/{api_version}/drivers/teeth/vendor_passthru/lookup'.format(
            api_version=self.api_version
        )
        # This hardware won't be saved on the node currently, because of
        # how driver_vendor_passthru is implemented (no node saving).
        data = {
            'hardware': hardware_info
        }

        # Make the POST, make sure we get back normal data/status codes and
        # content
        try:
            response = self._request('POST', path, data=data)
        except Exception as e:
            self.log.warning('POST failed: %s' % str(e))
            return next_interval(timeout, intervals, total_time)

        if response.status_code != requests.codes.OK:
            self.log.warning('Invalid status code: %s' %
                             response.status_code)

            return next_interval(timeout, intervals, total_time)

        try:
            content = json.loads(response.content)
        except Exception as e:
            self.log.warning('Error decoding response: %s' % str(e))
            return next_interval(timeout, intervals, total_time)

        # Check for valid response data
        if 'node' not in content or 'uuid' not in content['node']:
            self.log.warning('Got invalid node data from the API: %s' %
                             content)
            return next_interval(timeout, intervals, total_time)

        if 'heartbeat_timeout' not in content:
            self.log.warning('Got invalid heartbeat from the API: %s' %
                             content)
            return next_interval(timeout, intervals, total_time)

        # Got valid content
        raise loopingcall.LoopingCallDone(retvalue=content)

    def _get_agent_url(self, advertise_address):
        return 'http://{0}:{1}'.format(advertise_address[0],
                                       advertise_address[1])
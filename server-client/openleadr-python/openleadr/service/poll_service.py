# SPDX-License-Identifier: Apache-2.0

import asyncio
import logging
from openleadr import objects, utils
from openleadr.service.decorators import service, handler
from openleadr.service.vtn_service import VTNService

logger = logging.getLogger('openleadr')


@service('OadrPoll')
class PollService(VTNService):

    def __init__(self, vtn_id, polling_method='internal', event_service=None, report_service=None):
        super().__init__(vtn_id)
        self.polling_method = polling_method
        self.events_updated = {}
        self.report_requests = {}
        self.event_service = event_service
        self.report_service = report_service

    @handler('oadrPoll')
    async def poll(self, payload):
        ven_id = payload['ven_id']
        if self.polling_method == 'external':
            result = self.on_poll(ven_id=ven_id)
            if asyncio.iscoroutine(result):
                result = await result
            if result is None:
                response_payload = {
                    'response': {
                        'request_id': payload.get('request_id'),
                        'response_code': 200,
                        'response_description': 'OK'
                    }
                }
                return 'oadrResponse', response_payload
            if isinstance(result, tuple):
                return result
            if isinstance(result, (objects.Event, dict)):
                return 'oadrDistributeEvent', {'events': [result]}
            if isinstance(result, list):
                return 'oadrDistributeEvent', {'events': result}
            logger.warning(f"Unhandled poll result: {type(result)}")
            response_payload = {
                'response': {
                    'request_id': payload.get('request_id'),
                    'response_code': 200,
                    'response_description': 'OK'
                }
            }
            return 'oadrResponse', response_payload
        else:
            if self.events_updated.get(ven_id, False) and self.event_service:
                res = await self.event_service.request_event({'ven_id': ven_id})
                self.events_updated[ven_id] = False
                return res
            response_payload = {
                'response': {
                    'request_id': payload.get('request_id'),
                    'response_code': 200,
                    'response_description': 'OK'
                }
            }
            return 'oadrResponse', response_payload

    def on_poll(self, ven_id):
        logger.warning("No on_poll handler registered.")
        return None
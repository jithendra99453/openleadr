# SPDX-License-Identifier: Apache-2.0

import asyncio
import logging
from openleadr import utils, errors, enums
from openleadr.service.decorators import service, handler
from openleadr.service.vtn_service import VTNService

logger = logging.getLogger('openleadr')


@service('EiEvent')
class EventService(VTNService):

    def __init__(self, vtn_id, polling_method='internal'):
        super().__init__(vtn_id)
        self.polling_method = polling_method
        self.events = {}
        self.completed_event_ids = {}
        self.event_callbacks = {}
        self.event_opt_types = {}
        self.event_delivery_callbacks = {}

    @handler('oadrRequestEvent')
    async def request_event(self, payload):
        ven_id = payload['ven_id']
        if self.polling_method == 'internal':
            if ven_id in self.events and self.events[ven_id]:
                events = utils.order_events(self.events[ven_id])
                for event in events:
                    if utils.getmember(event, 'event_descriptor.event_status') == enums.EVENT_STATUS.COMPLETED:
                        if ven_id not in self.completed_event_ids:
                            self.completed_event_ids[ven_id] = []
                        event_id = utils.getmember(event, 'event_descriptor.event_id')
                        self.completed_event_ids[ven_id].append(event_id)
                        self.events[ven_id].pop(self.events[ven_id].index(event))
            else:
                events = None
        else:
            result = self.on_request_event(ven_id=ven_id)
            if asyncio.iscoroutine(result):
                result = await result
            events = utils.order_events(result) if result else None

        if events is None:
            # Return oadrResponse with a proper response object
            response_payload = {
                'response': {
                    'request_id': payload.get('request_id'),
                    'response_code': 200,
                    'response_description': 'OK'
                }
            }
            return 'oadrResponse', response_payload
        else:
            for event in events:
                event_id = utils.getmember(event, 'event_descriptor.event_id')
                if event_id in self.event_delivery_callbacks:
                    await utils.await_if_required(self.event_delivery_callbacks[event_id]())
            return 'oadrDistributeEvent', {'events': events}

    def on_request_event(self, ven_id):
        logger.warning("No on_request_event handler registered. Returning None.")
        return None

    @handler('oadrCreatedEvent')
    async def created_event(self, payload):
        ven_id = payload['ven_id']
        if self.polling_method == 'internal':
            for event_response in payload['event_responses']:
                event_id = event_response['event_id']
                mod_num = event_response['modification_number']
                opt_type = event_response['opt_type']
                event = utils.find_by(self.events[ven_id],
                                      'event_descriptor.event_id', event_id,
                                      'event_descriptor.modification_number', mod_num)
                if not event:
                    if event_id not in self.completed_event_ids.get(ven_id, []):
                        logger.warning(f"Unknown event {event_id} from {ven_id}")
                        continue
                if utils.getmember(event, 'event_descriptor.event_status') == enums.EVENT_STATUS.CANCELLED:
                    utils.pop_by(self.events[ven_id], 'event_descriptor.event_id', event_id)
                if event_id in self.event_callbacks:
                    ev, cb = self.event_callbacks.pop(event_id)
                    if isinstance(cb, asyncio.Future):
                        if not cb.done():
                            cb.set_result(opt_type)
                    else:
                        res = cb(ven_id=ven_id, event_id=event_id, opt_type=opt_type)
                        if asyncio.iscoroutine(res):
                            await res
        else:
            for event_response in payload['event_responses']:
                await utils.await_if_required(self.on_created_event(
                    ven_id=ven_id,
                    event_id=event_response['event_id'],
                    opt_type=event_response['opt_type']
                ))
        # Return oadrResponse with a response object
        response_payload = {
            'response': {
                'request_id': payload.get('request_id'),
                'response_code': 200,
                'response_description': 'OK'
            }
        }
        return 'oadrResponse', response_payload

    def on_created_event(self, ven_id, event_id, opt_type):
        logger.warning("No on_created_event handler registered.")
        return None
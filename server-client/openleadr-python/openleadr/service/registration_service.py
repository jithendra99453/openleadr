# SPDX-License-Identifier: Apache-2.0

from openleadr.service.decorators import service, handler
from openleadr.service.vtn_service import VTNService
from asyncio import iscoroutine
import logging

logger = logging.getLogger('openleadr')


@service('EiRegisterParty')
class RegistrationService(VTNService):

    def __init__(self, vtn_id, poll_freq):
        super().__init__(vtn_id)
        self.poll_freq = poll_freq

    @handler('oadrQueryRegistration')
    async def query_registration(self, payload):
        if hasattr(self, 'on_query_registration'):
            result = self.on_query_registration(payload)
            if iscoroutine(result):
                result = await result
            return result

        response_payload = {
            'request_id': payload.get('request_id'),
            'profiles': [
                {
                    'profile_name': '2.0b',
                    'transports': [{'transport_name': 'simpleHttp'}]
                }
            ],
            'requested_oadr_poll_freq': self.poll_freq,
            'response': {
                'request_id': payload.get('request_id'),
                'response_code': 200,
                'response_description': 'OK'
            }
        }
        return 'oadrCreatedPartyRegistration', response_payload

    @handler('oadrCreatePartyRegistration')
    async def create_party_registration(self, payload):
        result = self.on_create_party_registration(payload)
        if iscoroutine(result):
            result = await result

        base_response = {
            'request_id': payload.get('request_id'),
            'response_code': 200,
            'response_description': 'OK'
        }

        if result is False or result is None:
            response_payload = {
                'profiles': [
                    {
                        'profile_name': payload.get('profile_name', '2.0b'),
                        'transports': [{'transport_name': payload.get('transport_name', 'simpleHttp')}]
                    }
                ],
                'requested_oadr_poll_freq': self.poll_freq,
                'response': base_response
            }
        else:
            if not isinstance(result, tuple) or len(result) != 2:
                logger.error("Your on_create_party_registration handler must return False/None or (ven_id, registration_id). Rejecting.")
                response_payload = {
                    'profiles': [
                        {
                            'profile_name': payload.get('profile_name', '2.0b'),
                            'transports': [{'transport_name': payload.get('transport_name', 'simpleHttp')}]
                        }
                    ],
                    'requested_oadr_poll_freq': self.poll_freq,
                    'response': base_response
                }
            else:
                ven_id, registration_id = result
                response_payload = {
                    'ven_id': ven_id,
                    'registration_id': registration_id,
                    'profiles': [
                        {
                            'profile_name': payload.get('profile_name', '2.0b'),
                            'transports': [{'transport_name': payload.get('transport_name', 'simpleHttp')}]
                        }
                    ],
                    'requested_oadr_poll_freq': self.poll_freq,
                    'response': base_response
                }
        return 'oadrCreatedPartyRegistration', response_payload

    def on_create_party_registration(self, payload):
        logger.warning("No on_create_party_registration handler registered. Rejecting VEN.")
        return False

    @handler('oadrCancelPartyRegistration')
    async def cancel_party_registration(self, payload):
        ven_id = payload.get('ven_id')
        result = self.on_cancel_party_registration(ven_id)
        if iscoroutine(result):
            await result
        response_payload = {
            'response': {
                'request_id': payload.get('request_id'),
                'response_code': 200,
                'response_description': 'OK'
            }
        }
        return 'oadrCanceledPartyRegistration', response_payload

    def on_cancel_party_registration(self, ven_id):
        logger.warning("No on_cancel_party_registration handler registered.")
        return None
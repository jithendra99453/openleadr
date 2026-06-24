# SPDX-License-Identifier: Apache-2.0

import inspect
import logging
from asyncio import iscoroutine
from aiohttp import web
from openleadr.messaging import parse_message

logger = logging.getLogger('openleadr')


class VTNService:
    """Base class for all VTN services."""
    verify_message_signatures = False
    fingerprint_lookup = None
    ven_lookup = None
    _create_message = None

    def __init__(self, vtn_id):
        self.vtn_id = vtn_id

    async def handler(self, request):
        """Entry point for all HTTP POST requests to this service."""
        data = await request.read()
        message_type, payload = parse_message(data)
        # Find method decorated with @handler(message_type)
        for name, method in inspect.getmembers(self, inspect.ismethod):
            if hasattr(method, '__message_type__') and method.__message_type__ == message_type:
                result = method(payload)
                if iscoroutine(result):
                    result = await result
                if isinstance(result, tuple):
                    response_type, response_payload = result
                else:
                    response_type, response_payload = 'oadrResponse', {}
                xml_response = self._create_message(response_type, **response_payload)
                return web.Response(text=xml_response, content_type='application/xml')
        raise NotImplementedError(f"No handler for {message_type}")
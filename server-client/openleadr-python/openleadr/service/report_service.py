# SPDX-License-Identifier: Apache-2.0

import inspect
import logging
import asyncio
from openleadr import utils, objects
from openleadr.service.decorators import service, handler
from openleadr.service.vtn_service import VTNService

logger = logging.getLogger('openleadr')


@service('EiReport')
class ReportService(VTNService):

    def __init__(self, vtn_id):
        super().__init__(vtn_id)
        self.report_callbacks = {}
        self.registered_reports = {}
        self.requested_reports = {}
        self.created_reports = {}

    @handler('oadrRegisterReport')
    async def register_report(self, payload):
        report_requests = []
        mode = 'compact' if all(k in inspect.signature(self.on_register_report).parameters
                                for k in ('ven_id','resource_id','measurement','min_sampling_interval',
                                          'max_sampling_interval','unit','scale')) else 'full'

        if not payload.get('reports'):
            response_payload = {
                'report_requests': [],
                'response': {
                    'request_id': payload.get('request_id'),
                    'response_code': 200,
                    'response_description': 'OK'
                }
            }
            return 'oadrRegisteredReport', response_payload

        for report in payload['reports']:
            ven_id = payload['ven_id']
            if ven_id not in self.registered_reports:
                self.registered_reports[ven_id] = []
            report_copy = report.copy()
            if report_copy['report_name'].startswith('METADATA_'):
                report_copy['report_name'] = report_copy['report_name'][9:]
            self.registered_reports[ven_id].append(report_copy)

            results = None
            name = report.get('report_name')
            if name == 'METADATA_TELEMETRY_STATUS':
                if mode == 'compact':
                    tasks = []
                    for rd in report.get('report_descriptions', []):
                        tasks.append(self.on_register_report(
                            ven_id=ven_id,
                            resource_id=rd.get('report_data_source', {}).get('resource_id'),
                            measurement='Status',
                            unit=None, scale=None,
                            min_sampling_interval=rd.get('sampling_rate', {}).get('min_period'),
                            max_sampling_interval=rd.get('sampling_rate', {}).get('max_period')
                        ))
                    results = await utils.gather_if_required(tasks)
                else:
                    results = await utils.await_if_required(self.on_register_report(report))
            elif name == 'METADATA_TELEMETRY_USAGE':
                if mode == 'compact':
                    tasks = []
                    for rd in report.get('report_descriptions', []):
                        m = rd.get('measurement', {})
                        tasks.append(self.on_register_report(
                            ven_id=ven_id,
                            resource_id=rd.get('report_data_source', {}).get('resource_id'),
                            measurement=m.get('description'),
                            unit=m.get('unit'), scale=m.get('scale'),
                            min_sampling_interval=rd.get('sampling_rate', {}).get('min_period'),
                            max_sampling_interval=rd.get('sampling_rate', {}).get('max_period')
                        ))
                    results = await utils.gather_if_required(tasks)
                else:
                    results = await utils.await_if_required(self.on_register_report(report))
            elif name in ('METADATA_HISTORY_USAGE', 'METADATA_HISTORY_GREENBUTTON'):
                report_requests.append(None)
                continue
            else:
                logger.warning(f"Unsupported report type {name}")
                report_requests.append(None)
                continue

            if results is not None:
                if not isinstance(results, list):
                    logger.error("Handler must return list of tuples or None")
                    results = None
                else:
                    if mode == 'compact':
                        descs = report.get('report_descriptions', [])
                        results = [(descs[i]['r_id'], *results[i]) for i in range(len(descs)) if isinstance(results[i], tuple)]
            report_requests.append(results)

        utils.validate_report_request_tuples(report_requests, mode=mode)

        oadr_report_requests = []
        for i, rrq in enumerate(report_requests):
            if not rrq or all(r is None for r in rrq):
                continue
            orig = payload['reports'][i]
            spec_id = orig['report_specifier_id']
            req_id = utils.generate_id()
            spec_payloads = []
            for rr in rrq:
                if len(rr) == 3:
                    r_id, cb, samp_int = rr
                    rep_int = samp_int
                else:
                    r_id, cb, samp_int, rep_int = rr
                desc = utils.find_by(orig.get('report_descriptions', []), 'r_id', r_id)
                if not desc:
                    continue
                spec_payloads.append(objects.SpecifierPayload(r_id=r_id, reading_type=desc['reading_type']))
                self.report_callbacks[(req_id, r_id)] = cb
            oadr_report_requests.append(objects.ReportRequest(
                report_request_id=req_id,
                report_specifier=objects.ReportSpecifier(
                    report_specifier_id=spec_id,
                    granularity=samp_int,
                    report_back_duration=rep_int,
                    specifier_payloads=spec_payloads
                )
            ))

        self.requested_reports[payload['ven_id']] = oadr_report_requests
        response_payload = {
            'report_requests': oadr_report_requests,
            'response': {
                'request_id': payload.get('request_id'),
                'response_code': 200,
                'response_description': 'OK'
            }
        }
        return 'oadrRegisteredReport', response_payload

    async def on_register_report(self, *args, **kwargs):
        logger.warning("No on_register_report handler registered.")
        return None

    @handler('oadrUpdateReport')
    async def update_report(self, payload):
        for report in payload.get('reports', []):
            req_id = report.get('report_request_id')
            if not req_id:
                continue
            intervals_by_r_id = utils.group_by(report.get('intervals', []), 'report_payload.r_id')
            for r_id, intervals in intervals_by_r_id.items():
                key = (req_id, r_id)
                if key in self.report_callbacks:
                    values = [(i['dtstart'], i['report_payload']['value']) for i in intervals]
                    res = self.report_callbacks[key](values)
                    if asyncio.iscoroutine(res):
                        await res
        response_payload = {
            'response': {
                'request_id': payload.get('request_id'),
                'response_code': 200,
                'response_description': 'OK'
            }
        }
        return 'oadrUpdatedReport', response_payload

    @handler('oadrCreatedReport')
    async def created_report(self, payload):
        await utils.await_if_required(self.on_created_report(payload))
        response_payload = {
            'response': {
                'request_id': payload.get('request_id'),
                'response_code': 200,
                'response_description': 'OK'
            }
        }
        return 'oadrResponse', response_payload

    async def on_created_report(self, payload):
        ven_id = payload.get('ven_id')
        if ven_id not in self.created_reports:
            self.created_reports[ven_id] = []
        for pr in payload.get('pending_reports', []):
            rid = pr.get('report_request_id')
            if rid:
                self.created_reports[ven_id].append(rid)
        for req in self.requested_reports.get(ven_id, []):
            if req.report_request_id not in self.created_reports.get(ven_id, []):
                logger.warning(f"Report request {req.report_request_id} not created by VEN")
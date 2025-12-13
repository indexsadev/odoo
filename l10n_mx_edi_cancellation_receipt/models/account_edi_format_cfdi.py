# -*- encoding: utf-8 -*-
###########################################################################
#    Indexsa Technologies
#    Copyright (C) 2022 Indexsa Technologies
#    @author Indexsa Technologies
###########################################################################
from odoo import _, models, api, tools  # type: ignore
import json
import base64
import logging
from datetime import datetime
from pytz import timezone
import xml.etree.ElementTree as ET
from odoo.tools.zeep import Client, Transport # type: ignore


_logger = logging.getLogger(__name__)

MOTIVOS_CANCELACION = {
    '01': 'Comprobante emitido con errores con relación',
    '02': 'Comprobante emitido con errores sin relación',
    '03': 'No se llevó a cabo la operación',
    '04': 'Operación nominativa relacionada en la factura global',
}

class AccountEdiXmlFormat(models.Model):
    _inherit = 'l10n_mx_edi.document'


    def _fetch_sat_status_mod(self, supplier_rfc, customer_rfc, total, uuid):
        url = 'https://consultaqr.facturaelectronica.sat.gob.mx/ConsultaCFDIService.svc?wsdl'
        params = f'?id={uuid or ""}' \
                 f'&re={tools.html_escape(supplier_rfc or "")}' \
                 f'&rr={tools.html_escape(customer_rfc or "")}' \
                 f'&tt={total or 0.0}'
        transport = Transport(timeout=20)

        try:
            client = Client(wsdl=url, transport=transport)
            response = client.service.Consulta(params)
            es_cancelable = response['EsCancelable'] if hasattr(response, 'Estado') else ''
            estatus_cancelacion = response['EstatusCancelacion'] if hasattr(response, 'Estado') else ''
            sat_state = response['Estado'] if hasattr(response, 'Estado') else ''
            # pylint: disable=broad-except
        except Exception as e:
            return {
               'isCancelable': False,
               'EstatusCancelacion': '',
               'Estado': '',
            }

        return {'isCancelable': es_cancelable, 'EstatusCancelacion': estatus_cancelacion, 'Estado': sat_state}

    def _cancel_api(self, company, cancel_reason, on_failure, on_success):
        attachment_id = self.attachment_id
        cfdi_infos = self._decode_cfdi_attachment(attachment_id.raw) or {}

        return super(AccountEdiXmlFormat, self.with_context(
                cfdi_infos=cfdi_infos,
                cancel_api=True,
                move=self.move_id
            )
        )._cancel_api(company, cancel_reason, on_failure, on_success)

    def _proccess_acuse(self, acuse_xml, response, payload, pac_data, doc):
        try:
            root = ET.fromstring(acuse_xml)
            ns = {
                'sat': 'http://cancelacfd.sat.gob.mx',
                'ds': 'http://www.w3.org/2000/09/xmldsig#'
            }
            fecha_solicitud_raw = root.attrib.get('Fecha', '')
            emisor_rfc = root.attrib.get('RfcEmisor', '')
            uuid_node = root.find('.//sat:Folios/sat:UUID', ns)
            uuid_val = uuid_node.text if uuid_node is not None else ''
            estatus_node = root.find('.//sat:Folios/sat:EstatusUUID', ns)
            estatus_val = estatus_node.text if estatus_node is not None else ''
            sello_node = root.find('.//ds:SignatureValue', ns)
            sello_val = sello_node.text if sello_node is not None else ''
        except Exception as e:
            _logger.warning("Error parsing acuse XML: %s", e)
            return response

        fecha_solicitud = self._format_sat_datetime(fecha_solicitud_raw)
        emisor_rfc = root.attrib.get('RfcEmisor', '')
        if pac_data.get('emisor_rfc_override'):
            emisor_rfc = pac_data['emisor_rfc_override']

        payload_json = {}
        if payload:
            try:
                if isinstance(payload, bytes):
                    payload_json = json.loads(payload.decode('utf-8'))
                else:
                    payload_json = json.loads(payload)
            except Exception as e:
                _logger.warning("Error parsing payload: %s", e)

        motivo_codigo = payload_json.get('motivo', '')
        folio_sustituye = payload_json.get('folioSustitucion', '')
        
        motivo_texto = MOTIVOS_CANCELACION.get(motivo_codigo, '')
        motivo_cancelacion = f"{motivo_codigo} - {motivo_texto}" if motivo_texto else motivo_codigo
        
        status_sat = pac_data.get('statusSat', '')
        status_cancelation_sat = pac_data.get('status_Cancelation', '')
        is_cancelable = pac_data.get('isCancelable', '')
        
        status_sat = is_cancelable or status_cancelation_sat
            
        data_acuse = {
            'estatus_cancelacion': estatus_val,
            'uuid': uuid_val,
            'emisor_rfc': emisor_rfc,
            'fecha_solicitud': fecha_solicitud,
            'motivo_cancelacion': motivo_cancelacion,
            'sello_digital_sat': sello_val,
            'folio_sustituye': folio_sustituye,
            'acuse_xml': acuse_xml,
            'status_sat': status_sat,
            'status_code_sat': pac_data.get('statusCodeSat', ''),
            'is_cancelable': is_cancelable,
            'status_cancelation_sat': status_cancelation_sat,
        }

        if not doc or not doc.exists():
            return response
            
        # Si es de un pago, anexar al pago
        payment = self.env['account.payment'].search([('move_id', '=', doc.id)], limit=1)
        if payment:
            doc = payment

        fname_xml = ('%s-Acuse.xml' % (doc.name)).replace('/', '')
        acuse_xml_attach = self.env['ir.attachment'].create({
            'name': fname_xml,
            'res_model': doc._name,
            'res_id': doc.id,
            'type': 'binary',
            'datas': base64.b64encode(acuse_xml.encode('utf-8')),
            'mimetype': 'application/xml',
        })

        report_ref = 'l10n_mx_edi_cancellation_receipt.acuse_pdf'
        try:
            pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
                report_ref,
                doc.ids,
                data={'data': data_acuse}
            )
            fname_pdf = ('%s-Acuse.pdf' % (doc.name)).replace('/', '')
            acuse_pdf_attach = self.env['ir.attachment'].create({
                'name': fname_pdf,
                'res_model': doc._name,
                'res_id': doc.id,
                'type': 'binary',
                'datas': base64.b64encode(pdf_content),
                'mimetype': 'application/pdf',
            })
            doc.message_post(
                body="Acuse de solicitud de cancelación",
                attachment_ids=[acuse_xml_attach.id, acuse_pdf_attach.id]
            )
        except Exception as e:
             _logger.warning("Error generating PDF acuse: %s", e)
             doc.message_post(
                body="Acuse de solicitud de cancelación (sin PDF)",
                attachment_ids=[acuse_xml_attach.id]
            )
        return response

    def _format_sat_datetime(self, value):
        if not value:
            return ''
        try:
            value = value.split('.')[0]
            dt = datetime.fromisoformat(value)
            return dt.strftime('%d/%m/%Y %H:%M:%S')
        except Exception:
            return value

    # ========================================================================
    # PAC - SW Sapiens
    # ========================================================================

    @api.model
    def _get_sw_credentials(self, company):
        credentials = super(AccountEdiXmlFormat, self)._get_sw_credentials(company)
        if 'errors' in credentials:
            return credentials
        if company.l10n_mx_edi_pac_test_env:
            credentials['cancel_url'] = 'https://services.test.sw.com.mx/cfdi33/cancel/csd/status'
        else:
            credentials['cancel_url'] = 'https://services.sw.com.mx/cfdi33/cancel/csd/status'
        return credentials
    

    def _document_sw_call(self, url, headers, payload=None):
        if self.env.context.get("cancel_api"):
            payload = self._enhance_cancel_payload(payload)
        response = super()._document_sw_call(url, headers, payload=payload)
        
        if not self.env.context.get("cancel_api"): 
            return response
        try:
            _logger.info(json.dumps(response, indent=2, ensure_ascii=False))
        except Exception:
            pass

        if response is None or not (isinstance(response, dict) and 'cancel' in url.lower()):
            return response

        pac_data = response.get('data') or {}
        acuse_xml = pac_data.get('acuse')

        if not acuse_xml:
            return response
        
        doc = self.env.context.get("move")
        self._proccess_acuse(acuse_xml, response, payload, pac_data, doc)
        return response

    def _enhance_cancel_payload(self, payload):
        cfdi_infos = self.env.context.get('cfdi_infos')
        try:
            if isinstance(payload, bytes):
                payload_dict = json.loads(payload.decode('utf-8'))
            else:
                payload_dict = json.loads(payload) if payload else {}
        except Exception:
            return payload

        if cfdi_infos:
            total = cfdi_infos.get('amount_total')
            rfc_receptor = cfdi_infos.get('customer_rfc')
            if total and not payload_dict.get('total'):
                payload_dict['total'] = str(total)
            if rfc_receptor and not payload_dict.get('rfcReceptor'):
                payload_dict['rfcReceptor'] = rfc_receptor

        try:
            return json.dumps(payload_dict, ensure_ascii=False).encode('utf-8')
        except Exception:
            return payload

    # ========================================================================
    # PAC - FINKOK
    # ========================================================================
    
    @api.model
    def _finkok_cancel(self, cfdi_values, credentials, uuid, cancel_reason, cancel_uuid=None):
        if not self.env.context.get('cancel_api'):
            return super()._finkok_cancel(cfdi_values, credentials, uuid, cancel_reason, cancel_uuid=cancel_uuid)
        return self._finkok_cancel_acuse(cfdi_values, credentials, uuid, cancel_reason, cancel_uuid=cancel_uuid)
    
    @api.model
    def _finkok_consultar_estado(self, cfdi_values, credentials, uuid, rfc_emisor, rfc_receptor, total):
        company = cfdi_values['root_company']
        if not rfc_emisor:
            rfc_emisor = company.vat
        if not total:
            total = ''
        
        try:           
            client = Client(credentials['cancel_url']) 
            response = client.service.get_sat_status(
                credentials['username'],
                credentials['password'],
                rfc_emisor, 
                rfc_receptor,
                uuid,
                str(total) if total else ''
            )
            
            sat_data = getattr(response, 'sat', None)
            es_cancelable = getattr(sat_data, 'EsCancelable', '')
            estado_sat = getattr(sat_data, 'Estado', '')
            estatus_cancelacion = getattr(sat_data, 'EstatusCancelacion', '')
            detalles_validacion = getattr(sat_data, 'DetallesValidacionEFOS', '')
            validacion_efos = getattr(sat_data, 'ValidacionEFOS', '')
            codigo_estatus = getattr(sat_data, 'CodigoEstatus', '')

            status_response = {
                'uuid': uuid,
                'es_cancelable': es_cancelable,
                'estado': estado_sat,
                'estatus_cancelacion': estatus_cancelacion,
                'detalles_validacion_efos': detalles_validacion,
                'validacion_efos': validacion_efos,
                'codigo_estatus': codigo_estatus
            }
            
            return status_response
        
        except Exception as e:
            _logger.error("Error en consulta de estado Finkok: %s", str(e))
            return {
                'errors': [_("Error al consultar estado en Finkok: %s", str(e))],
                'uuid': uuid,
                'status_sat': 'No Cancelado',
            }


    def _extract_and_clean_finkok_acuse(self, acuse_xml_raw):
        try:
            acuse_xml_corregido = acuse_xml_raw.encode('latin-1').decode('utf-8')
            root = ET.fromstring(acuse_xml_corregido)
            ns = {
                's': 'http://schemas.xmlsoap.org/soap/envelope/',
                'default': 'http://cancelacfd.sat.gob.mx'
            }

            cancelacfd_result = root.find('.//default:CancelaCFDResult', ns)
            acuse_xml_limpio = ET.tostring(cancelacfd_result, encoding='unicode') # type: ignore

            return acuse_xml_limpio

        except Exception as exc:
            _logger.warning("Error procesando acuse FINKOK: %s", str(exc))
        return acuse_xml_raw

    @api.model
    def _finkok_cancel_acuse(self, cfdi_values, credentials, uuid, cancel_reason, cancel_uuid=None):
        company = cfdi_values['root_company']
        certificate_sudo = cfdi_values['certificate'].sudo()
        cer_pem = base64.b64decode(certificate_sudo.pem_certificate)
        key_pem = self._get_unencrypted_private_key_pem(certificate_sudo.private_key_id)

        try:
            client = Client(credentials['cancel_url'], timeout=20)
            factory = client.type_factory('apps.services.soap.core.views')
            uuid_type = factory.UUID()
            uuid_type.UUID = uuid
            uuid_type.Motivo = cancel_reason
            if cancel_uuid:
                uuid_type.FolioSustitucion = cancel_uuid
            docs_list = factory.UUIDArray(uuid_type)
            response = client.service.cancel(
                docs_list,
                credentials['username'],
                credentials['password'],
                company.vat,
                cer_pem,
                key_pem,
            )
        except Exception as e:
            return {
                'errors': [_("The Finkok service failed to cancel with the following error: %s", str(e))],
            }
        

        code = None
        msg = None
        if 'Folios' in response and response.Folios:
            if 'EstatusUUID' in response.Folios.Folio[0]:
                response_code = response.Folios.Folio[0].EstatusUUID
                if response_code not in ('201', '202'):
                    code = response_code
                    msg = _("Cancelling got an error")
        elif 'CodEstatus' in response:
            code = response.CodEstatus
            msg = _("Cancelling got an error")
        else:
            msg = _('A delay of 2 hours has to be respected before to cancel')

        errors = []
        if code:
            errors.append(_("Code : %s", code))
        if msg:
            errors.append(_("Message : %s", msg))
        if errors:
            return {'errors': errors}
        
        acuse_xml_raw = response.Acuse if hasattr(response, 'Acuse') else None
    
        if acuse_xml_raw:
            acuse_xml_limpio = self._extract_and_clean_finkok_acuse(acuse_xml_raw)
            cfdi_infos = self.env.context.get('cfdi_infos') or {}
            rfc_emisor = cfdi_infos.get('supplier_rfc', '')
            rfc_receptor = cfdi_infos.get('customer_rfc', '')
            total = cfdi_infos.get('amount_total', '')
            
            status_response = self._finkok_consultar_estado(
                cfdi_values=cfdi_values,
                credentials=credentials,
                uuid=uuid,
                rfc_emisor=rfc_emisor,
                rfc_receptor=rfc_receptor,
                total=total,
            )
            
            status_sat = status_response.get('estado', '')
            es_cancelable = status_response.get('es_cancelable', '')
            estatus_cancelacion = status_response.get('estatus_cancelacion', '')
            
            pac_data = {
                'acuse': acuse_xml_raw,
                'statusSat': status_sat,
                'isCancelable': es_cancelable,
                'status_Cancelation': estatus_cancelacion,
                'emisor_rfc_override': rfc_emisor,
                'statusCodeSat': response.Folios.Folio[0].EstatusUUID if hasattr(response.Folios.Folio[0], 'EstatusUUID') else '',
            }
        
            response_compatible = {
                'data': pac_data
            }
        
            payload_finkok = json.dumps({
                'motivo': cancel_reason,
                'folioSustitucion': cancel_uuid or '',
            }).encode('utf-8')

            move = self.env.context.get('move')
            self.with_context(move=move)._proccess_acuse(acuse_xml_limpio, response_compatible, payload_finkok, pac_data, move)
        
        return {}

    # ========================================================================
    # PAC - SOLUCION FACTIBLE
    # ========================================================================
    
    @api.model
    def _solfact_cancel(self, cfdi_values, credentials, uuid, cancel_reason, cancel_uuid=None):
        if not self.env.context.get('cancel_api'):
            return super()._solfact_cancel(cfdi_values, credentials, uuid, cancel_reason, cancel_uuid=cancel_uuid)
        return self._solfact_cancel_acuse(cfdi_values, credentials, uuid, cancel_reason, cancel_uuid=cancel_uuid)

    @api.model
    def _solfact_cancel_acuse(self, cfdi_values, credentials, uuid, cancel_reason, cancel_uuid=None):
        _logger.info("="*80)
        _logger.info("CANCELACIÓN SOLFACT")
        _logger.info("="*80)

        cdmx_tz = timezone('America/Mexico_City')
        fecha_hora_solicitud = datetime.now(cdmx_tz).strftime('%Y-%m-%dT%H:%M:%S')

        certificate = cfdi_values['certificate']
        uuid_param = f"{uuid}|{cancel_reason}|"
        if cancel_uuid:
            uuid_param += cancel_uuid

        cer_pem = base64.b64decode(certificate.pem_certificate)
        key_pem = self._get_unencrypted_private_key_pem(certificate.private_key_id)
        key_password = certificate.private_key_id.password

        try:
            client = Client(credentials['url'], timeout=20)
            response = client.service.cancelar(
                credentials['username'], credentials['password'],
                uuid_param, cer_pem, key_pem, key_password
            )
        except Exception as e:
            _logger.error("Error en solicitud de cancelación: %s", e)
            return {'errors': [_("The Solucion Factible service failed to cancel: %s", str(e))]}

        if response.resultados:
            result = response.resultados[0]
            response_code = str(getattr(result, 'statusUUID', response.status))
            mensaje = getattr(result, 'mensaje', "")
        else:
            response_code = str(getattr(response, "status", ""))
            mensaje = getattr(response, "mensaje", "")

        _logger.info("StatusUUID: %s", response_code)
        CANCEL_SUCCESS_CODES = ['201', '202']
        is_cancelled_success = response_code in CANCEL_SUCCESS_CODES

        if not is_cancelled_success:
            _logger.error("Cancelación rechazada: %s", mensaje)
            return {'errors': [mensaje or _("Cancelación rechazada con código: %s", response_code)]}


        cfdi_infos = self.env.context.get('cfdi_infos') or {}
        rfc_emisor = cfdi_infos.get('supplier_rfc', '')
        rfc_receptor = cfdi_infos.get('customer_rfc', '')
        total = cfdi_infos.get('amount_total', '')

        estado_sat = self._fetch_sat_status_mod(rfc_emisor, rfc_receptor, total, cfdi_infos['uuid'])
        is_cancelable = estado_sat.get('isCancelable', '')
        if self.env.company.test_mode and not is_cancelable:
            is_cancelable = 'Cancelable sin aceptacion'
            
        acuse_limpio = self.build_acuse_solucion_factible(response, rfc_emisor, cancel_reason=cancel_reason, fecha_hora=fecha_hora_solicitud)

        response_data = {
            'uuid': uuid,
            'cancel_status': response_code,
            'mensaje': mensaje,
            'consulta_estado': estado_sat,
            'acuse_xml': acuse_limpio,
            'rfc_emisor': rfc_emisor,
        }

        pac_data = {
            'statusSat': is_cancelable,
            'status_Cancelation': response_code,
            'isCancelable': is_cancelable,
            'statusCodeSat': estado_sat.get('Estado', ''),
            'emisor_rfc_override': None,
        }

        payload = json.dumps({'motivo': cancel_reason, 'folioSustitucion': cancel_uuid}).encode('utf-8')

        return self._proccess_acuse(
            acuse_xml=acuse_limpio,
            response=response_data,
            payload=payload,
            pac_data=pac_data,
            doc=self.env.context.get('move')
        )

    @api.model
    def build_acuse_solucion_factible(self, resultado_json, rfc_emisor, cancel_reason=None, fecha_hora=None):
        resultado = resultado_json['resultados'][0]

        # Extraer los componentes del acuse del mensaje
        mensaje = resultado['mensaje']

        # Extraer Acuse, Digest y Certificado
        acuse_val = ''
        digest_val = ''
        certificado_val = ''

        if 'Acuse:' in mensaje:
            acuse_val = mensaje.split('Acuse:')[1].split(';')[0].strip()
        if 'Digest:' in mensaje:
            digest_val = mensaje.split('Digest:')[1].split(';')[0].strip()
        if 'Certificado:' in mensaje:
            certificado_val = mensaje.split('Certificado:')[1].strip()

        motivo_texto = f"{cancel_reason} - {MOTIVOS_CANCELACION.get(cancel_reason,'')}" if cancel_reason else ''

        fecha_hora_iso = fecha_hora or datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        
        root = ET.Element('CancelaCFDResult', {
            'xmlns': 'http://cancelacfd.sat.gob.mx',
            'Fecha': fecha_hora_iso,
            'RfcEmisor': rfc_emisor,
        })

        # Agregar Folios
        folios = ET.SubElement(root, 'Folios')
        ET.SubElement(folios, 'UUID').text = resultado['uuid']
        ET.SubElement(folios, 'EstatusUUID').text = resultado['statusUUID']

        # Agregar Signature
        signature = ET.SubElement(root, 'Signature', {'xmlns': 'http://www.w3.org/2000/09/xmldsig#', 'Id': 'SelloSAT'})
        signed_info = ET.SubElement(signature, 'SignedInfo')
        reference = ET.SubElement(signed_info, 'Reference', {'URI': ''})
        ET.SubElement(reference, 'DigestMethod', {'Algorithm': 'http://www.w3.org/2001/04/xmlenc#sha512'})
        ET.SubElement(reference, 'DigestValue').text = digest_val.replace('\n', '')
        ET.SubElement(signature, 'SignatureValue').text = acuse_val.replace('\n', '')
        key_info = ET.SubElement(signature, 'KeyInfo')
        ET.SubElement(key_info, 'KeyName').text = certificado_val

        # Nodo para motivo de cancelación
        ET.SubElement(root, 'MotivoCancelacion').text = motivo_texto

        # Convertir a string XML
        return ET.tostring(root, encoding='unicode')
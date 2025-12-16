# -*- encoding: utf-8 -*-
###########################################################################
#    Indexsa Technologies
#    Copyright (C) 2022 Indexsa Technologies
#    @author Indexsa Technologies
###########################################################################
import json
import base64
import logging
from datetime import datetime
from pytz import timezone
import xml.etree.ElementTree as ET
from odoo.tools.zeep import Client, Transport # type: ignore
from odoo import _, models, api, tools  # type: ignore


_logger = logging.getLogger(__name__)

MOTIVOS_CANCELACION = {
    '01': 'Comprobante emitido con errores con relación',
    '02': 'Comprobante emitido con errores sin relación',
    '03': 'No se llevó a cabo la operación',
    '04': 'Operación nominativa relacionada en la factura global',
}

class AccountEdiXmlFormat(models.Model):
    _inherit = 'l10n_mx_edi.document'

    # ========================================================================
    # Extended methods
    # ========================================================================
    

    def _fetch_sat_status(self, supplier_rfc, customer_rfc, total, uuid):
        """ Override para obtener estatus completo del SAT. """
        # Llamar a _fetch_sat_status_full para más detalles
        results = self._fetch_sat_status_full(supplier_rfc, customer_rfc, total, uuid)
        
        # Determine standard 'value' based on 'statusSat'
        status_sat = results.get('statusSat', '')
        if status_sat == 'Vigente':
            results['value'] = 'valid'
        elif status_sat == 'Cancelado':
            results['value'] = 'cancelled'
        elif status_sat == 'No Encontrado':
            results['value'] = 'not_found'
        else:
            results['value'] = 'not_defined'
            
        return results

    def _update_sat_state(self):
        """ Override para pasar resultados completos a _update_document_sat_state. """
        self.ensure_one()
        
        # Just in case, ensure we have the attachment processed
        cfdi_infos = self.env['l10n_mx_edi.document']._decode_cfdi_attachment(self.attachment_id.raw)
        if not cfdi_infos:
            return

        # Obtener estatus mejorado
        sat_results = self._fetch_sat_status(
            cfdi_infos['supplier_rfc'],
            cfdi_infos['customer_rfc'],
            cfdi_infos['amount_total'],
            cfdi_infos['uuid'],
        )

        # Actualizar si cambió estado o si está pendiente (detectar rechazo)
        # Nota: Pasamos sat_results a _update_document_sat_state
        if self.sat_state != sat_results['value'] or self.state in ('invoice_cancel_requested', 'payment_cancel', 'ginvoice_cancel'):
             self._update_document_sat_state(sat_results['value'], error=sat_results.get('error'), sat_values=sat_results)

             if self._can_commit():
                self.env.cr.commit()

    def _fetch_sat_status_full(self, supplier_rfc, customer_rfc, total, uuid):
        url = 'https://consultaqr.facturaelectronica.sat.gob.mx/ConsultaCFDIService.svc?wsdl'
        params = f'?id={uuid or ""}' \
                 f'&re={tools.html_escape(supplier_rfc or "")}' \
                 f'&rr={tools.html_escape(customer_rfc or "")}' \
                 f'&tt={total or 0.0}'
        transport = Transport(timeout=20)

        try:
            client = Client(wsdl=url, transport=transport)
            response = client.service.Consulta(params)
            es_cancelable = response['EsCancelable'] if hasattr(response, 'EsCancelable') else ''
            estatus_cancelacion = response['EstatusCancelacion'] if hasattr(response, 'EstatusCancelacion') else ''
            sat_state = response['Estado'] if hasattr(response, 'Estado') else ''
            codigo_estatus = response['CodigoEstatus'] if hasattr(response, 'CodigoEstatus') else ''
        except Exception as e:
            return {
               'isCancelable': False,
               'statusSat': '',
               'statusCancelation': '',
               'statusCodeSat': '',
               'error': str(e),
            }

        return {
            'isCancelable': es_cancelable,
            'statusSat': sat_state,
            'statusCancelation': estatus_cancelacion,
            'statusCodeSat': codigo_estatus,
        }

    def _cancel_api(self, company, cancel_reason, on_failure, on_success):
        attachment_id = self.attachment_id
        cfdi_infos = self._decode_cfdi_attachment(attachment_id.raw) or {}

        return super(AccountEdiXmlFormat, self.with_context(
                cfdi_infos=cfdi_infos,
                cancel_api=True,
                move=self.move_id
            )
        )._cancel_api(company, cancel_reason, on_failure, on_success)


    def _update_document_sat_state(self, sat_state, error=None, sat_values=None):
        """ Actualiza el doc con el nuevo estado del SAT.

        :param sat_state: Estado SAT retornado por '_fetch_sat_status'.
        :param error:       Mensaje de error del SAT.
        :param sat_values:  Diccionario completo SAT (opcional)

        Llamado por _update_sat_state()
        """
        self.ensure_one()
        
        # Guardar estados previos y referencias antes de llamar al super
        status_anterior = self.state
        sat_state_anterior = self.sat_state
        move = self.move_id
        payment = None
        if self.state in ('payment_sent', 'payment_cancel') and move:
            payment = self.env['account.payment'].search([('move_id', '=', move.id)], limit=1)
        
        # Llamar al método padre para actualizar el estado (sin sat_values)
        result = super(AccountEdiXmlFormat, self)._update_document_sat_state(sat_state, error=error)
        
        # Verificar si el documento actual sigue existiendo
        doc_actual = self
        if not self.exists():
            if move:
                docs = move.l10n_mx_edi_invoice_document_ids.sorted(lambda d: d.create_date, reverse=True)
                doc_actual = docs[0] if docs else None
                
            if not doc_actual and payment:
                 pass

        if not doc_actual or not doc_actual.exists():
            return result

        # Detectar si hubo cambio de estado relevante usando el doc actual
        cambio_estado = doc_actual.state != status_anterior
        
        estados_indefinidos = ('not_defined', 'not_found', 'error')
        sat_anterior_indefinido = sat_state_anterior in estados_indefinidos
        sat_actual_indefinido = doc_actual.sat_state in estados_indefinidos
        
        cambio_sat_state_significativo = (
            (not sat_anterior_indefinido and not sat_actual_indefinido and doc_actual.sat_state != sat_state_anterior)
            or (sat_anterior_indefinido != sat_actual_indefinido)
        )
        
        # Si tenemos valores SAT, validar si justica regeneración (ej. Vigente pero Rechazada)
        if sat_values and doc_actual.state == 'invoice_cancel_requested':
             # En producción llamar comportamiento normal es Rechazada
             if sat_values.get('statusCancelation', '') == 'Solicitud rechazada':
                 cambio_sat_state_significativo = True

        # Regenerar PDF si corresponde
        if (cambio_estado or cambio_sat_state_significativo) and doc_actual.state in ('invoice_cancel', 'invoice_cancel_requested', 'payment_cancel', 'ginvoice_cancel'):
            target_obj = None
            if move and doc_actual.state in ('invoice_sent', 'invoice_cancel', 'invoice_cancel_requested', 'invoice_received'):
                target_obj = move
            elif payment and doc_actual.state in ('payment_sent', 'payment_cancel'):
                target_obj = payment
            elif doc_actual.state in ('ginvoice_sent', 'ginvoice_cancel'):
                source_records = doc_actual._get_source_records()
                target_obj = source_records[0] if source_records else None
            
            if target_obj and target_obj.exists():
                doc_actual._update_acuse_cancelacion(target_obj, sat_state, sat_values=sat_values)
        
        return result

    # ========================================================================
    # Common methods
    # ========================================================================

    def _update_acuse_cancelacion(self, doc, sat_state, sat_values=None):
        """ Actualiza el acuse de cancelación. """
        self.ensure_one()

        # Verificar que el documento CFDI esté en estado de cancelación o cancelación solicitada
        if self.state not in ('invoice_cancel', 'invoice_cancel_requested', 'payment_cancel', 'ginvoice_cancel'):
            return

        cfdi_infos = self.env['l10n_mx_edi.document']._decode_cfdi_attachment(self.attachment_id.raw)
        if not cfdi_infos:
            return

        if sat_values:
            sat_results = sat_values
        else:
            sat_results = self._fetch_sat_status_full(
                cfdi_infos['supplier_rfc'],
                cfdi_infos['customer_rfc'],
                cfdi_infos['amount_total'],
                cfdi_infos['uuid'],
            )

        status_sat = sat_results.get('statusSat') or sat_results.get('Estado') # Fallback keys
        
        if status_sat:
            # Buscar XML de cancelacion entre los anexos del doc
            data_de_acuse = self.get_data_acuse_cancelacion(doc)

            emisor_rfc = data_de_acuse.get('emisor_rfc', '')
            fecha_solicitud = data_de_acuse.get('fecha_solicitud', '')
            sello_val = data_de_acuse.get('sello_val', '')

            motivo_codigo = self.cancellation_reason # sat_results.get('motivo', '')
            substitution_doc = self._get_substitution_document()
            folio_sustituye = substitution_doc.attachment_uuid or ''
            
            motivo_texto = MOTIVOS_CANCELACION.get(motivo_codigo, '')
            motivo_cancelacion = f"{motivo_codigo} - {motivo_texto}" if motivo_texto else motivo_codigo
            
            status_sat = sat_results.get('statusSat', '')
            status_cancelation_sat = sat_results.get('statusCancelation', '')
            is_cancelable = sat_results.get('isCancelable', '')
            
            data_acuse = {
                'uuid': self.attachment_uuid,
                'emisor_rfc': emisor_rfc,
                'fecha_solicitud': fecha_solicitud,
                'motivo_cancelacion': motivo_cancelacion,
                'sello_digital_sat': sello_val,
                'folio_sustituye': folio_sustituye,
                'status_sat': status_sat,
                'status_code_sat': sat_results.get('statusCodeSat', ''),
                'is_cancelable': is_cancelable,
                'status_cancelation_sat': status_cancelation_sat,
            }

            if not doc or not doc.exists():
                return 
                
            # Si es de un pago, anexar al pago
            payment = self.env['account.payment'].search([('move_id', '=', doc.id)], limit=1)
            if payment:
                doc = payment

            self._generate_acuse_pdf(doc, data_acuse, [])

              
    def get_data_acuse_cancelacion(self, doc):

        # Buscar XML de cancelacion entre los anexos del doc

        fname_xml = ('%s-Acuse.xml' % (doc.name)).replace('/', '')
        attachments = self.env['ir.attachment'].search([
            ('res_model', '=', doc._name),
            ('res_id', '=', doc.id),
            ('mimetype', '=', 'application/xml'),
            ('name', '=', fname_xml),
        ])

        attach_xml = False

        for attachment in attachments:
            try:
                xml_content = base64.b64decode(attachment.datas)
                root = ET.fromstring(xml_content)
                # Check for common root tags or namespaces indicating a cancellation receipt (Acuse)
                # Example: <Acuse> or <AcuseDeCancelacion>
                if root.tag.endswith('Acuse') or root.tag.endswith('AcuseDeCancelacion'):
                    _logger.debug("Found cancellation XML attachment: %s for document %s", attachment.name, doc.name)
                    attach_xml = attachment
                    break
            except ET.ParseError:
                _logger.warning("Could not parse XML attachment %s for document %s", attachment.name, doc.name)
            except Exception as e:
                _logger.error("Error processing attachment %s for document %s: %s", attachment.name, doc.name, e)

        if attach_xml:
            acuse_xml = base64.b64decode(attach_xml.datas)
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
                return {}

            fecha_solicitud = self._format_sat_datetime(fecha_solicitud_raw)
            emisor_rfc = root.attrib.get('RfcEmisor', '')

            return {
                'fecha_solicitud': fecha_solicitud,
                'emisor_rfc': emisor_rfc,
                'sello_val': sello_val,
            }
            
        return {}

    def _proccess_acuse(self, acuse_xml, response, payload, status_cancelation, doc):
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
        if status_cancelation.get('emisor_rfc_override'):
            emisor_rfc = status_cancelation['emisor_rfc_override']

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
        
        status_sat = status_cancelation.get('statusSat', '')
        status_cancelation_sat = status_cancelation.get('statusCancelation', '')
        is_cancelable = status_cancelation.get('isCancelable', '')
        
        data_acuse = {
            'uuid': uuid_val,
            'emisor_rfc': emisor_rfc,
            'fecha_solicitud': fecha_solicitud,
            'motivo_cancelacion': motivo_cancelacion,
            'sello_digital_sat': sello_val,
            'folio_sustituye': folio_sustituye,
            'acuse_xml': acuse_xml,
            'status_sat': status_sat,
            'status_code_sat': status_cancelation.get('statusCodeSat', ''),
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

        self._generate_acuse_pdf(doc, data_acuse, [acuse_xml_attach.id])

        return response

    def _generate_acuse_pdf(self, doc, data_acuse, attachment_ids):
        #
        # Generar PDF de Acuse
        #

        if data_acuse.get('status_sat') == 'Cancelado' and not data_acuse.get('status_cancelation_sat'):
            if data_acuse.get('isCancelable', '') == 'Cancelable con aceptacion':
                data_acuse['status_cancelation_sat'] = 'Cancelado Con Aceptación'
            else:
                data_acuse['status_cancelation_sat'] = 'Cancelado Sin Aceptación'

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

            attachment_ids.append(acuse_pdf_attach.id)

            doc.message_post(
                body="Acuse de solicitud de cancelación",
                attachment_ids=attachment_ids
            )
        except Exception as e:
             _logger.warning("Error generating PDF acuse: %s", e)
             if attachment_ids:
                 doc.message_post(
                    body="Acuse de solicitud de cancelación (sin PDF)",
                    attachment_ids=attachment_ids
                )

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
        
        if _logger.isEnabledFor(logging.DEBUG):
            try:
                _logger.debug(json.dumps(response, indent=2, ensure_ascii=False))
            except Exception:
                pass

        if response is None or not (isinstance(response, dict) and 'cancel' in url.lower()):
            return response

        pac_data = response.get('data') or {}
        acuse_xml = pac_data.get('acuse')

        if acuse_xml:
            self._proccess_acuse(acuse_xml, response, payload, pac_data, self.env.context.get("move"))

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
                'isCancelable': es_cancelable,
                'statusSat': estado_sat,
                'statusCancelation': estatus_cancelacion,
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
        response_status_cancelation = None
        if 'Folios' in response and response.Folios:
            if 'EstatusCancelacion' in response.Folios.Folio[0]:
                response_status_cancelation = response.Folios.Folio[0].EstatusCancelacion
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
            
            status_sat = status_response.get('statusSat', '')
            es_cancelable = status_response.get('isCancelable', '')
            estatus_cancelacion = status_response.get('statusCancelation', '') or response_status_cancelation or ''
            
            status_cancelation = {
                'acuse': acuse_xml_raw,
                'statusSat': status_sat,
                'isCancelable': es_cancelable,
                'statusCancelation': estatus_cancelacion,
                'emisor_rfc_override': rfc_emisor,
                'statusCodeSat': response.Folios.Folio[0].EstatusUUID if hasattr(response.Folios.Folio[0], 'EstatusUUID') else '',
            }
        
            response_compatible = {
                'data': status_cancelation
            }
        
            payload_finkok = json.dumps({
                'motivo': cancel_reason,
                'folioSustitucion': cancel_uuid or '',
            }).encode('utf-8')

            move = self.env.context.get('move')
            self.with_context(move=move)._proccess_acuse(acuse_xml_limpio, response_compatible, payload_finkok, status_cancelation, move)
        
        return {}

    # ========================================================================
    # PAC - SOLUCION FACTIBLE
    # ========================================================================
    
    @api.model
    def _solfact_cancel(self, cfdi_values, credentials, uuid, cancel_reason, cancel_uuid=None):
        if not self.env.context.get('cancel_api'):
            return super()._solfact_cancel(cfdi_values, credentials, uuid, cancel_reason, cancel_uuid=cancel_uuid)
        return self._solfact_cancel_acuse(cfdi_values, credentials, uuid, cancel_reason, cancel_uuid=cancel_uuid)

    def _solfact_get_status_async(self, credentials, uuid):
        """
        Call getStatusCancelacionAsincrona to retrieve the cancellation status and acuse.
        """
        if 'testing.solucionfactible.com' in credentials['url']:
            url = 'https://testing.solucionfactible.com/ws/services/Cancelacion?wsdl'
        else:
            url = 'https://solucionfactible.com/ws/services/Cancelacion?wsdl'

        try:
            client = Client(url, timeout=20)
            # The default ports in the WSDL might have empty locations (causing "Invalid URL ''").
            # We explicitly bind to the HTTPS port which has a valid location.
            service = client.bind('Cancelacion', 'CancelacionHttpsSoap11Endpoint')
            response_status = service.getStatusCancelacionAsincrona(
                credentials['username'],
                credentials['password'],
                uuid
            )
            return response_status
        except Exception as e:
            _logger.error("Error calling getStatusCancelacionAsincrona: %s", e)
            return None

    @api.model
    def _solfact_cancel_acuse(self, cfdi_values, credentials, uuid, cancel_reason, cancel_uuid=None):

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

        _logger.debug("StatusUUID: %s", response_code)


        cfdi_infos = self.env.context.get('cfdi_infos') or {}
        rfc_emisor = cfdi_infos.get('supplier_rfc', '')
        rfc_receptor = cfdi_infos.get('customer_rfc', '')
        total = cfdi_infos.get('amount_total', '')
        
        # Consultar el estado asíncrono para obtener el acuse oficial
        async_response = self._solfact_get_status_async(credentials, uuid)
        
        acuse_xml_str = ''
        status_cancelacion_sat = ''
        is_cancelable = ''
        estado_sat = ''

        if async_response:
            # Si el estatus es de 'En proceso'
            if hasattr(async_response, 'mensaje') and async_response.mensaje and '<?xml' in async_response.mensaje:
                acuse_xml_str = async_response.mensaje
            elif hasattr(async_response, 'resultados') and async_response.resultados:
                pass

        if async_response and hasattr(async_response, 'mensaje') and async_response.mensaje and async_response.mensaje.startswith('<'):
            # Acuse del async.
            acuse_limpio = async_response.mensaje
        else:
            # Fallback si no hay acuse oficial aun (ej. en proceso).
            acuse_limpio = self.build_acuse_solucion_factible(response, rfc_emisor, cancel_reason=cancel_reason, fecha_hora=fecha_hora_solicitud)


        CANCEL_SUCCESS_CODES = ['201', '202', '200'] 
        is_cancelled_success = response_code in CANCEL_SUCCESS_CODES

        if not is_cancelled_success:
            _logger.error("Cancelación rechazada: %s", mensaje)
            return {'errors': [mensaje or _("Cancelación rechazada con código: %s", response_code)]}

        # Consulta al SAT
        estado_sat_info = self._fetch_sat_status_full(rfc_emisor, rfc_receptor, total, cfdi_infos['uuid'])
        is_cancelable = estado_sat_info.get('isCancelable', '')
            
        # Acuse correcto
        if not acuse_xml_str and acuse_limpio:
            acuse_xml_str = acuse_limpio

        response_data = {
            'uuid': uuid,
            'cancel_status': response_code,
            'mensaje': mensaje,
            'consulta_estado': estado_sat_info,
            'acuse_xml': acuse_xml_str,
            'rfc_emisor': rfc_emisor,
        }

        status_cancelation = {
            'statusSat': is_cancelable,
            'statusCancelation': response_code,
            'isCancelable': is_cancelable,
            'statusCodeSat': estado_sat_info.get('statusSat', ''),
            'emisor_rfc_override': None,
        }

        payload = json.dumps({'motivo': cancel_reason, 'folioSustitucion': cancel_uuid}).encode('utf-8')

        return self._proccess_acuse(
            acuse_xml=acuse_xml_str,
            response=response_data,
            payload=payload,
            status_cancelation=status_cancelation,
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

        rfc_emisor = rfc_emisor or self.env.context.get('cfdi_infos', {}).get('supplier_rfc', '') or ''
        
        # Root Element "Acuse"
        root = ET.Element('Acuse', {
            'xmlns:xsd': 'http://www.w3.org/2001/XMLSchema',
            'xmlns:xsi': 'http://www.w3.org/2001/XMLSchema-instance',
            'Fecha': fecha_hora_iso,
            'RfcEmisor': rfc_emisor
        })

        # Folios element with explicit namespace
        folios = ET.SubElement(root, 'Folios', {'xmlns': 'http://cancelacfd.sat.gob.mx'})
        ET.SubElement(folios, 'UUID').text = resultado['uuid'] or ''
        ET.SubElement(folios, 'EstatusUUID').text = resultado['statusUUID'] or ''

        # Signature
        signature = ET.SubElement(root, 'Signature', {'xmlns': 'http://www.w3.org/2000/09/xmldsig#'})
        signed_info = ET.SubElement(signature, 'SignedInfo')
        
        # Canonicalization and SignatureMethod (Standard values)
        ET.SubElement(signed_info, 'CanonicalizationMethod', {'Algorithm': 'http://www.w3.org/TR/2001/REC-xml-c14n-20010315'})
        ET.SubElement(signed_info, 'SignatureMethod', {'Algorithm': 'http://www.w3.org/2000/09/xmldsig#rsa-sha1'})

        reference = ET.SubElement(signed_info, 'Reference', {'URI': ''})
        
        # Transforms
        transforms = ET.SubElement(reference, 'Transforms')
        ET.SubElement(transforms, 'Transform', {'Algorithm': 'http://www.w3.org/2000/09/xmldsig#enveloped-signature'})

        ET.SubElement(reference, 'DigestMethod', {'Algorithm': 'http://www.w3.org/2001/04/xmlenc#sha512'})
        ET.SubElement(reference, 'DigestValue').text = (digest_val or '').replace('\n', '')

        ET.SubElement(signature, 'SignatureValue').text = (acuse_val or '').replace('\n', '')
        
        key_info = ET.SubElement(signature, 'KeyInfo')
        ET.SubElement(key_info, 'KeyName').text = certificado_val or ''

        # Convertir a string XML
        # return ET.tostring(root, encoding='utf-8')
        return ET.tostring(root, encoding='unicode')


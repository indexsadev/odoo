# -*- coding: utf-8 -*-
from unittest.mock import patch, MagicMock
from odoo.tests import TransactionCase, tagged
from odoo.exceptions import UserError

@tagged('post_install', '-at_install')
class TestCancellationReceipt(TransactionCase):

    def setUp(self):
        super(TestCancellationReceipt, self).setUp()
        self.move = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.env.ref('base.res_partner_12').id,
            'date': '2023-01-01',
            'currency_id': self.env.ref('base.MXN').id,
        })
        # Simulate a signed document
        self.edi_document = self.env['l10n_mx_edi.document'].create({
            'move_id': self.move.id,
            'datetime': '2023-01-01 12:00:00',
            'state': 'invoice_sent',
            'sat_state': 'valid',
            'attachment_uuid': '12345678-1234-1234-1234-123456789012',
        })
        self.edi_document.attachment_id = self.env['ir.attachment'].create({
            'name': 'test.xml',
            'datas': b'PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0iVVRGLTgiPz4KPGNmbGRpOkNvbXByb2JhbnRlIHhtbG5zOmNmbGRpPSJodHRwOi8vd3d3LnNhdC5nb2IubXgvY2ZkLzQiIFZlcnNpb249IjQuMCI+PC9jZmxkaTpDb21wcm9iYW50ZT4K', # Minimal valid base64 XML
            'mimetype': 'application/xml',
        })

    def test_silent_rejection(self):
        """ Prueba detección de rechazo silencioso (Vigente + Rechazada) """
        # Config: En solicitud de cancelación
        self.edi_document.state = 'invoice_cancel_requested'
        self.edi_document.sat_state = 'valid'

        # Simular rechazo SAT
        mock_sat_response = {
            'value': 'valid',
            'statusSat': 'Vigente',
            'statusCancelation': 'Solicitud rechazada',
            'isCancelable': 'Cancelable con aceptación',
            'statusCodeSat': 'S - Comprobante obtenido exitosamente.',
        }

        # Parchear clase
        model_class = type(self.env['l10n_mx_edi.document'])

        with patch.object(model_class, '_update_acuse_cancelacion') as mock_update_acuse:
            
            # Acción: Actualizar. Detectar rechazo aunque siga vigente
            self.edi_document._update_document_sat_state('valid', sat_values=mock_sat_response)

            # Verificar: Se regenera acuse
            mock_update_acuse.assert_called_once()

    def test_standard_approval(self):
        """ Prueba cancelación exitosa """
        self.edi_document.state = 'invoice_cancel_requested'
        self.edi_document.sat_state = 'valid'
        
        # Simular respuesta Cancelado
        mock_sat_response = {
            'value': 'cancelled',
            'statusSat': 'Cancelado',
            'statusCancelation': 'Cancelado con aceptación',
        }

        model_class = type(self.env['l10n_mx_edi.document'])

        with patch.object(model_class, '_update_acuse_cancelacion') as mock_update_acuse:
            
            # Acción: Actualizar a cancelado
            self.edi_document._update_document_sat_state('cancelled', sat_values=mock_sat_response)

            # Verificar: Se regenera acuse
            mock_update_acuse.assert_called_once()


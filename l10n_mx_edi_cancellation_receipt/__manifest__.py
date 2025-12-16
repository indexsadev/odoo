{
	'name': 'Mexico - SAT Cancellation Acknowledgement',
	'summary': 'Descarga y resguardo automático de acuses de cancelación CFDI 4.0 del SAT',
	'version': '19.0.1.0.1',
	'description': """
		Gestión y Resguardo del Acuse de Cancelación SAT (CFDI 4.0)
		=============================================================
		
		Este módulo complementa el flujo nativo de cancelación en Odoo (v19+), permitiendo 
		descargar y adjuntar automáticamente el PDF y XML del Acuse de Cancelación oficial 
		emitido por el SAT.
		
		Características principales:
		* Descarga automática del XML y PDF del Acuse de Cancelación
		* Adjunto automático al registro de la factura en el chatter
		* Compatibilidad con SW Sapiens, Finkok y Solución Factible
		* Soporte para los 4 motivos de cancelación del SAT (01, 02, 03, 04)
		* Validación del estado del CFDI en el SAT
		* Cumple con la normativa fiscal mexicana vigente
		* Facilita auditorías y compulsas fiscales
	""",
	'depends': [
		'account',
		'account_edi',
		'l10n_mx_edi'
	],
	'data': [
		'reports/acuse_view.xml',
		'views/acuse_pdf_view.xml',
	],
	'images': [
		'static/description/icon.png',
		'static/description/screenshot_1.png',
		'static/description/screenshot_2.png',
		'static/description/screenshot_3.png',
	],
	'author': 'Indexsa Technologies',
	'maintainer': 'Indexsa Technologies',
	'website': 'https://www.indexsa.io',
	'support': 'contacto@indexsa.io',
	'license': 'OPL-1',
	'category': 'Accounting/Localizations/EDI',
	'price': 0.00,
	'currency': 'USD',
	
	'installable': True,
	'application': False,
	'auto_install': False,
} # type: ignore
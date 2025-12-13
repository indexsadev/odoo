# Mexico - SAT Cancellation Acknowledgement (CFDI 4.0)

## Descripción

Este módulo extiende las capacidades de la localización mexicana de Odoo para garantizar el cumplimiento fiscal estricto mediante el **resguardo automático del Acuse de Cancelación (PDF y XML)** emitido por el SAT.

### Funcionalidades Principales

- ✅ **Descarga automática** del XML y PDF del Acuse de Cancelación
- ✅ **Adjunto automático** al registro de la factura en el chatter
- ✅ **Compatibilidad** con los 3 principales PACs:
  - SW Sapiens
  - Finkok
  - Solución Factible
- ✅ **Validación del estado del CFDI** en el SAT
- ✅ **Trazabilidad fiscal completa** para auditorías

## Contexto Fiscal

La cancelación de un CFDI no es solo un proceso administrativo, es una **obligación fiscal crítica** establecida por el SAT:

- **Validez Fiscal**: Cancelar una factura anula su existencia legal
- **Plazos**: La cancelación debe realizarse a más tardar el **31 de enero del año siguiente** a su emisión
- **Evidencia**: Es indispensable contar con el acuse oficial que certifique que el folio fiscal (UUID) ha sido cancelado correctamente

Aunque Odoo gestiona la solicitud de cancelación, las empresas mexicanas **requieren tener el archivo físico (PDF y XML)** del acuse para responder ante auditorías o compulsas. Este módulo automatiza ese paso.

## Requisitos

- Odoo 19.0 Enterprise
- Módulo `l10n_mx_edi` instalado y configurado
- Cuenta activa con uno de los PACs soportados (SW, Finkok o Solución Factible)
- Certificados SAT (CSD) vigentes

## Instalación

1. Descarga o clona este módulo en tu directorio de addons personalizados
2. Actualiza la lista de módulos en Odoo:
   ```
   Aplicaciones > Actualizar lista de aplicaciones
   ```
3. Busca "SAT Cancellation Acknowledgement" o "l10n_mx_edi_cancellation_receipt"
4. Haz clic en **Instalar**

## Configuración

No requiere configuración adicional. El módulo funciona automáticamente una vez instalado, utilizando la configuración existente de tu PAC en:

```
Contabilidad > Configuración > Ajustes > Facturación Electrónica Mexicana
```

## Uso

### Cancelar una Factura

1. Ve al registro de la factura que deseas cancelar
2. Haz clic en el botón **"Solicitar cancelación de EDI"**
3. Selecciona el **motivo de cancelación** (01, 02, 03 o 04)
4. Si es motivo 01, ingresa el **UUID de la factura que sustituye**
5. Confirma la cancelación

### Visualizar el Acuse

Una vez procesada la cancelación exitosamente:

1. El módulo descarga automáticamente el XML del acuse del PAC
2. Genera un PDF profesional con la información del acuse
3. Adjunta ambos archivos (XML y PDF) en el **chatter** de la factura
4. Puedes descargar los archivos en cualquier momento desde el chatter

El PDF incluye:
- Fecha y hora de la solicitud
- RFC del emisor
- Folio fiscal (UUID)
- Estatus del proceso de cancelación
- Motivo de cancelación
- CFDI que reemplaza (si aplica)
- Sello digital del SAT

## Compatibilidad

| Versión Odoo | Compatible |
|--------------|------------|
| 19.0         | ✅ Sí      |
| 18.0         | ❌ No      |
| 17.0         | ❌ No      |

## Soporte Técnico

Para soporte técnico, reportes de errores o solicitudes de nuevas funcionalidades:

- **Email**: [contacto@indexsa.io]
- **Website**: https://www.indexsa.io

## Autor

**Indexsa Technologies**

- Website: https://www.indexsa.io

## Licencia

Este módulo está licenciado bajo **OPL-1** (Odoo Proprietary License).

## Changelog

### Versión 19.0.1.0.0
- Lanzamiento inicial
- Soporte para SW Sapiens, Finkok y Solución Factible
- Generación automática de PDF del acuse
- Descarga y adjunto automático de XML
- Validación de estado del CFDI en SAT

## Créditos

Este módulo fue desarrollado por **Indexsa Technologies** para facilitar el cumplimiento fiscal de empresas mexicanas que utilizan Odoo.

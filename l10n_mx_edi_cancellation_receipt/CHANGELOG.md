# Changelog

Todos los cambios notables en este proyecto serán documentados en este archivo.

El formato está basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/),
y este proyecto adhiere a [Semantic Versioning](https://semver.org/lang/es/).

## [19.0.1.0.0] - 2025-12-13

### Agregado
- Lanzamiento inicial del módulo
- Soporte para descarga automática de Acuse de Cancelación (XML)
- Generación automática de PDF del Acuse de Cancelación
- Adjunto automático de XML y PDF en el chatter de la factura
- Compatibilidad con PAC SW Sapiens
- Compatibilidad con PAC Finkok
- Compatibilidad con PAC Solución Factible
- Validación del estado del CFDI en el SAT
- Extracción y procesamiento del sello digital SAT

### Técnico
- Herencia del modelo `l10n_mx_edi.document`
- Override de métodos de cancelación por PAC:
  - `_document_sw_call()` para SW Sapiens
  - `_finkok_cancel()` para Finkok
  - `_solfact_cancel()` para Solución Factible
- Consulta de estado SAT vía SOAP/REST según PAC
- Construcción de XML de acuse para Solución Factible

### Documentación
- README.md con instrucciones de instalación y uso
- Descripción HTML
- Este archivo CHANGELOG.md
---

## Formato de Versionado

Este módulo usa versionado en formato Odoo: `{ODOO_VERSION}.{MAJOR}.{MINOR}.{PATCH}`

- **ODOO_VERSION**: Versión de Odoo (ej: 19.0)
- **MAJOR**: Cambios incompatibles en la API
- **MINOR**: Nueva funcionalidad compatible hacia atrás
- **PATCH**: Correcciones de bugs compatibles hacia atrás

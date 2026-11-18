# 🛰️ Manual Técnico y Funcional para Humanos — `kanan-cacao-geo-engine`

**Repositorio:** `kanan-cacao-geo-engine` (Backend Central Compartido por Web y App Móvil)  
**Producción:** [https://kanan-cacao-geo-engine-production.up.railway.app](https://kanan-cacao-geo-engine-production.up.railway.app)  
**Centro Maestro de Documentación:** [`kanan-cacao/docs/00-INDICE-MAESTRO-DOCUMENTACION.md`](../../kanan-cacao/docs/00-INDICE-MAESTRO-DOCUMENTACION.md)

---

## 1. Propósito de Este Repositorio (El Cerebro Compartido)

`kanan-cacao-geo-engine` es el microservicio backend desarrollado en **Python 3.11 + FastAPI + Shapely + SQLite/PostGIS**. Es el **repositorio central que comparten la página web (`kanan-cacao-web`) y la aplicación móvil (`kanan-cacao-mobile`)**.

Gracias a este motor:
1. Una parcela creada o eliminada en la Web o en la App Móvil se refleja en ambos sistemas.
2. Un documento legal subido desde cualquiera de las dos plataformas recibe su firma criptográfica **SHA-256** y queda disponible en la bóveda digital.
3. Un registro de **Bitácora** guardado en campo desde el celular se sincroniza con la Web, y cualquier cambio que el Administrador haga en el catálogo de Bitácora desde la Web se descarga automáticamente en la App Móvil.

---

## 2. Estructura de Carpetas y Archivos Explicada para Humanos

```text
kanan-cacao-geo-engine/
├── app/
│   ├── main.py                             # Punto de entrada FastAPI, CORS y registro de rutas
│   ├── api/
│   │   ├── routes.py                       # Endpoints de topología Shapely, NDVI, EUDR y TRACES-NT
│   │   ├── routes_persistence.py           # Endpoints de Parcelas, Documentos SHA-256 y Bitácora
│   │   └── routes_admin_roles.py           # Endpoints RBAC Admin y Configuración de Bitácora
│   ├── models/
│   │   └── schemas.py                      # Esquemas Pydantic v2 de validación de entrada/salida
│   └── services/
│       ├── local_persistence_engine.py     # Motor SQL de parcelas, bitácora y Archivado Inteligente
│       ├── topology_engine.py              # Validador geométrico RFC 7946 con Shapely y Geod
│       ├── satellite_engine.py             # Cálculo de NDVI Sentinel-2 y línea base JRC 2020
│       └── custody_engine.py               # Verificación CUSTF, Yield Caps y firma SHA-256 EPCIS
├── tests/
│   ├── test_logbook_configurable.py        # Pruebas del catálogo configurable, RBAC y Archivado Inteligente
│   ├── test_logbook_persistence.py         # Pruebas CRUD y sincronización offline-online de Bitácora
│   └── ...                                 # Total: 32 pruebas automatizadas en Pytest
└── Dockerfile                              # Imagen contenedor para despliegue en Railway y pruebas locales
```

---

## 3. Catálogo de Endpoints que Comparten Web y Móvil

### A. Parcelas y Documentación Legal (`app/api/routes_persistence.py`)
* `GET /api/v1/parcels`: Lista todas las parcelas registradas con sus coordenadas WGS84 y estatus EUDR.
* `POST /api/v1/parcels`: Crea o actualiza una parcela.
* `DELETE /api/v1/parcels/{parcel_id}`: Elimina una parcela en ambos sistemas.
* `POST /api/v1/parcels/sync`: Sincroniza lotes de parcelas capturadas offline en el celular.
* `GET /api/v1/parcels/{parcel_id}/documents`: Lista el expediente legal de la parcela.
* `POST /api/v1/parcels/{parcel_id}/documents`: Sube un documento legal y genera su hash **SHA-256**.

### B. Bitácora Operativa de Campo (`app/api/routes_persistence.py`)
* `GET /api/v1/logbook/config`: Devuelve la configuración activa de la Bitácora (los 6 interruptores globales, las 8 labores agronómicas activas no archivadas y los bioinsumos sugeridos).
* `GET /api/v1/logbook`: Lista el historial de labores con filtros por `parcel_id`, `producer_id`, `start_date` y `end_date`.
* `POST /api/v1/logbook`: Registra una nueva labor agronómica con insumo, dosis, horas de trabajo, jornaleros, costo MXN, clima, notas y fotos.
* `PATCH /api/v1/logbook/{entry_id}`: **Edición Reactiva (`✏️ Editar`)** de cualquier registro existente.
* `DELETE /api/v1/logbook/{entry_id}`: Elimina un registro de bitácora.
* `POST /api/v1/logbook/sync`: Sincroniza bidireccionalmente los registros offline de la App Móvil con el servidor.

### C. Panel de Administración de Bitácora (`app/api/routes_admin_roles.py`)
Protegidos por `verify_logbook_config_requester` (requiere rol `admin` o permiso `can_configure_logbook`):
* `GET /api/v1/admin/logbook/config`: Devuelve el catálogo completo incluyendo elementos archivados (`include_archived=True`).
* `PUT /api/v1/admin/logbook/toggles`: Activa o desactiva secciones de la bitácora (fotos, insumos, dosis, mano de obra/costos, clima).
* `POST /api/v1/admin/logbook/activities`: Crea o edita una labor agronómica en el catálogo.
* `DELETE /api/v1/admin/logbook/activities/{activity_key}`: Ejecuta el **Archivado Inteligente**: si la labor ya tiene registros históricos en `field_logbook_entries`, la marca como `is_archived = 1` y `is_active = 0` para preservar la historia; si no tiene registros, la elimina físicamente.

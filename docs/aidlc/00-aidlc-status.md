# AI-DLC Framework · Estado Actual Consolidado (`kanan-cacao-geo-engine`)

**Repositorio:** `kanan-cacao-geo-engine` (FastAPI + Python Geoespacial + Planetary Computer / WMTS + Supabase)  
**Última actualización:** `15/11/2026` · **Versión activa:** `v0.13.0` (`v9_cacaotrack_hd`)

---

## Registro de Iteraciones AI-DLC Completadas

| Iteración | Versión | Especificación AI-DLC | Estado | Resumen de Entregables Verificados |
| :--- | :--- | :--- | :--- | :--- |
| Bolt Inicial | `v0.8.0` | [`01-hybrid-satellite-cache-and-docs-spec.md`](./01-hybrid-satellite-cache-and-docs-spec.md) | Completado | Motor satelital híbrido (MPC Sentinel-2 L2A + Esri Wayback), persistencia única en servidor/Supabase Storage, horizonte fijo (`0m, 6m, 1a..5a`), subida/descarga de documentos legales por parcela. |
| Calidad & Auditoría | `v0.9.0` | [`02-high-res-guard-and-regdate-audit-spec.md`](./02-high-res-guard-and-regdate-audit-spec.md) | Completado | Guardián de nitidez espacial (`compute_image_sharpness`), endpoint administrativo de fecha de registro con confirmación `"confirmar"` (`HTTP 403`/`400`) y bitácora inmutable `registration_date_audit_log`. |
| Ventana en Días & Roles | `v0.10.0` | [`03-v5-days-margin-and-admin-roles-spec.md`](./03-v5-days-margin-and-admin-roles-spec.md) | Completado | Selección temporal estricta en margen de días con hasta 10 intentos anti-nubes, endpoints CRUD de roles editables y asignación de rol por perfil (`/api/v1/admin/roles`, `/api/v1/admin/profiles/{id}/role`). |
| Encuadre & Anti-Sombras | `v0.11.0` | [`04-v6-centered-20attempts-spec.md`](./04-v6-centered-20attempts-spec.md) | Completado | Detector de sombras de nube + nubes blancas/bruma ampliado a **20 intentos** consecutivos y normalización de encuadre centrado. |
| Zoom Dinámico | `v0.12.0` | [`05-v7-optimal-parcel-zoom-spec.md`](./05-v7-optimal-parcel-zoom-spec.md) | Completado | Función `compute_optimal_parcel_zoom` (`fill_ratio = 0.58`) adaptativa al tamaño en hectáreas y recortador fraccional `_render_wmts_fractional_zoom`. |
| **Alta Definición 2x** | **`v0.13.0`** | [`06-v8-hd-1520-pansharpened-spec.md`](./06-v8-hd-1520-pansharpened-spec.md) | **En Despliegue** | Ensamblado cuadro-a-cuadro **1520×840 (Full HD 2x)** + fusión pan-sharpened aditiva (`Sentinel-2` fecha exacta + detalle sub-métrico `0.34–0.60 m/px`) bajo caché `v8_hd_1520`. |

---

## Compuertas de Calidad

- **Tests (`pytest -v`):** 18/18 pasando (`test_custody_epcis`, `test_health`, `test_local_persistence`, `test_spatial_and_satellite`).
- **Lint (`flake8 app tests`):** 0 violaciones.
- **Producción (Railway):** Servicio `kanan-cacao-geo-engine` conectado al esquema Supabase PostGIS + Bucket `parcel-satellite-maps` & documentos legales.

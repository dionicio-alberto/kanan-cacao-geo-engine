# AI-DLC Framework · Estado Actual Consolidado (`kanan-cacao-geo-engine`)

**Repositorio:** `kanan-cacao-geo-engine` (FastAPI + Python Geoespacial + Planetary Computer / WMTS + Supabase)  
**Última actualización:** `16/11/2026` · **Versión activa:** `v0.14.0` (`v11_sentinel2_pure`)

---

## Registro de Iteraciones AI-DLC Completadas

| Iteración | Versión | Especificación AI-DLC | Estado | Resumen de Entregables Verificados |
| :--- | :--- | :--- | :--- | :--- |
| Bolt Inicial | `v0.8.0` | [`01-hybrid-satellite-cache-and-docs-spec.md`](./01-hybrid-satellite-cache-and-docs-spec.md) | Completado (`main`) | Motor satelital híbrido, persistencia única en servidor/Supabase Storage, horizonte fijo (`0m, 6m, 1a..5a`), subida/descarga de documentos legales por parcela. |
| Calidad & Auditoría | `v0.9.0` | [`02-high-res-guard-and-regdate-audit-spec.md`](./02-high-res-guard-and-regdate-audit-spec.md) | Completado (`main`) | Guardián de nitidez espacial (`compute_image_sharpness`), endpoint administrativo de fecha de registro con confirmación `"confirmar"` (`HTTP 403`/`400`) y bitácora inmutable `registration_date_audit_log`. |
| Ventana en Días & Roles | `v0.10.0` | [`03-v5-days-margin-and-admin-roles-spec.md`](./03-v5-days-margin-and-admin-roles-spec.md) | Completado (`main`) | Selección temporal estricta en margen de días con hasta 10 intentos anti-nubes, endpoints CRUD de roles editables y asignación de rol por perfil (`/api/v1/admin/roles`, `/api/v1/admin/profiles/{id}/role`). |
| Encuadre & Anti-Sombras | `v0.11.0` | [`04-v6-centered-20attempts-spec.md`](./04-v6-centered-20attempts-spec.md) | Completado (`main`) | Detector de sombras de nube + nubes blancas/bruma ampliado a **20 intentos** consecutivos y normalización de encuadre centrado. |
| Zoom Dinámico | `v0.12.0` | [`05-v7-optimal-parcel-zoom-spec.md`](./05-v7-optimal-parcel-zoom-spec.md) | Completado (`main`) | Función `compute_optimal_parcel_zoom` (`fill_ratio = 0.58`) adaptativa al tamaño en hectáreas y recortador fraccional `_render_wmts_fractional_zoom`. |
| Alta Definición 2x | `v0.13.0` | [`06-v8-hd-1520-pansharpened-spec.md`](./06-v8-hd-1520-pansharpened-spec.md) | Completado (`main`) | Ensamblado cuadro-a-cuadro **1520×840** y realce óptico 3×3 `apply_cacao_track_hd_enhancement`. |
| Auditoría Multitemporal | `v0.13.1` | [`11-georef-loading-and-satellite-multitemporal-spec.md`](../../../kanan-cacao-web/docs/aidlc/11-georef-loading-and-satellite-multitemporal-spec.md) | Completado (`main`) | Diagnóstico matemático de multitemporalidad satelital en Comalcalco (`Test Terreno`) y reutilización de caché en servidor. |
| **Estándar Sentinel-2** | `v0.13.2` | [`07-v10-sentinel2-standardization-spec.md`](./07-v10-sentinel2-standardization-spec.md) | Completado (`main`) | Estandarización soberana en **Copernicus Sentinel-2 L2A** (revisita 5 días, gratuito) + respaldo **Sentinel-2 Cloudless EOX ESA** y caché `v10_sentinel2_standard`. |
| **Histórico Puro S2** | **`v0.14.0`** | [`08-v11-sentinel2-pure-historic-fix-spec.md`](./08-v11-sentinel2-pure-historic-fix-spec.md) | **Completado (`main`)** | URL versionada `s2v11_`, 0 colisiones entre horizontes (`7/7` fechas/escenas/MD5 únicos), alineación `render_zoom` 1x en Sentinel-2 puro, hidratación paralela y `/parcels/refresh-historical`. |

---

## Compuertas de Calidad

- **Tests (`pytest -v`):** 18/18 pasando (`test_custody_epcis`, `test_health`, `test_local_persistence`, `test_spatial_and_satellite`).
- **Lint (`flake8 app tests`):** 0 violaciones.
- **Producción (Railway):** Servicio `kanan-cacao-geo-engine` activo (`HTTP 200`), conectado al esquema Supabase PostGIS + Bucket `parcel-satellite-maps` y documentos legales.

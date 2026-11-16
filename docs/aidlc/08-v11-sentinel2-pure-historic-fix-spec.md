# AI-DLC Spec v0.14.0 · Corrección de Histórico Satelital y 100% Copernicus Sentinel-2 (`v11_sentinel2_pure`)

**Metodología:** AI-Driven Development Life Cycle (AI-DLC) — Iteración `v0.14.0`  
**Rama:** `feature/v11-sentinel2-pure-historic-fix`

---

## 1. Diagnóstico en Producción y Causa Raíz

1. **Parcelas atrapadas en caché previo (`v9_cacaotrack_hd`):** 6 de las 7 parcelas en Supabase mantenían los 2–3 fondos estáticos de la versión previa (`v9`); en `GET /api/v1/parcels`, el motor intentaba reconstruir todas secuencialmente en una sola petición (> 60 s), agotando timeouts antes de persistir las actualizaciones en Supabase.
2. **Colisión de nombres en Supabase Storage/CDN:** El prefijo compartido `{parcel_id}/{period_key}_{safe_rel}.jpg` permitía que CDN/navegadores sirvieran imágenes `v9` anteriores cuando se reutilizaba un ID de escena `mpc:`.
3. **Escala `render_zoom` 2x en `_render_mpc_sentinel2_bicubic` y sobre-zoom `16.75`:** Retornaba `scale_adj` (`+1.0`) en lugar del zoom `1x` sincronizado con el SVG y ampliaba en exceso píxeles de `10 m/px`.
4. **Posible colisión entre horizontes y fallback no-Sentinel:** Sin control estricto de unicidad entre los 7 horizontes ni eliminación de `VERIFIED_TABASCO_CAPTURES` (Esri Wayback).

---

## 2. Implementación Técnica

- **Versión y URLs inmutables (`v11_sentinel2_pure` / prefijo `s2v11_`):** Tanto `upload_parcel_map_to_supabase_storage` como la caché local en disco usan el prefijo versionado `s2v11_` evitando servir cachés legadas.
- **100% Copernicus Sentinel-2:**
  - Prioridad 1: **Copernicus Sentinel-2 L2A** (Microsoft Planetary Computer STAC, revisita cada 5 días, búsqueda hasta **20 intentos** anti-nubes/sombras con ventanas acotadas disjuntas y deduplicación estricta `used_dates`/`used_releases`).
  - Prioridad 2 y contingencia offline: **Copernicus Sentinel-2 Cloudless (EOX ESA `s2cloudless-2025_3857` .. `2019_3857`)**, eliminando consultas a Google y Esri Wayback.
- **Alineación geométrica y claridad:** `_render_mpc_sentinel2_bicubic` limita a `min(15.65, optimal_zoom)` (render interno 2x `1520×840`, retornando `1x` `render_zoom` exacto para el polígono SVG).
- **Precalentamiento paralelo:** `_fetch_supabase_parcels` hidrata filas pendientes en paralelo (`ThreadPoolExecutor`) y se agrega `POST /api/v1/parcels/refresh-historical`.

---

## 3. Verificación

- `pytest -v` (**18/18** aprobadas) y `flake8 app tests` (**0** errores).
- Validación local con servidor `8080` y `3000`: **7/7 fechas distintas**, **7/7 escenas Sentinel-2 distintas** y **7/7 hashes MD5 de imagen distintos** por cada parcela.

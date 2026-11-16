# AI-DLC Spec v0.13.0 · Fusión Pan-Sharpened en Alta Definición (`1520×840`, `v8_hd_1520`)

**Metodología:** AI-Driven Development Life Cycle (AI-DLC) — Iteración `v0.13.0`
**Rama de trabajo:** `feature/v8-hd-1520-pansharpened`

---

## 1. Inception & Elaboration (Diagnóstico y Solución Técnica)

1. **Problema identificado:**
   - A nivel de parcela individual (`zoom ≈ 15.8 – 16.9`), las bandas nativas RGB de **Copernicus Sentinel-2 L2A** tienen una resolución espacial de **10 m/píxel**, cubriendo únicamente ~48 × 26 píxeles reales sobre el polígono. Al interpolarse a pantalla completa producían un aspecto pixelado o borroso.
   - Al mismo tiempo, las ortofotos públicas **Planet-NICFI** devuelven 0 escenas públicas en las coordenadas de Comalcalco/Cárdenas, y el archivo único de mosaico sub-métrico **Esri Wayback** no tiene una captura histórica distinta cada 6 meses / 1 año sincronizada con las fechas exactas de registro.

2. **Solución implementada (`v8_hd_1520` — Pan-Sharpening Aditivo de Alta Frecuencia + Tile Stitching 2x HiDPI):**
   - **Ensamblado cuadro por cuadro (`_stitch_wmts_fractional_image`):** En lugar de ampliar un cuadro único pequeño (`760×420`), el motor descarga y ensambla las teselas WMTS necesarias a escala **Retina 2x (`1520×840`)** y zoom continuo `rz_hd = optimal_zoom + 1.0`.
   - **Origen y técnica satelital adoptados desde `cacao-track` (`v9_cacaotrack_hd`):**
     - **Orígenes submétricos HD (`0.30 – 0.60 m/píxel`):** Google Satellite (`mt1.google.com/vt/lyrs=s`), ArcGIS Live `World_Imagery` y catálogo verificado **Esri Wayback** (`15212, 4383, 8495, 51313, 17825, 49059, 43291` + dinámico).
     - **Realce óptico idéntico a `cacao-track` (`apply_cacao_track_hd_enhancement`):** Ajuste radiométrico (`Contrast 1.08`, `Brightness 1.02`, `Color 1.12`) + filtro de convolución **Unsharp Mask 3×3** `[0, -0.26, 0, -0.26, 2.04, -0.26, 0, -0.26, 0]`.
     - **Fusión Pan-Sharpened (`fuse_sentinel2_with_submeter_detail`):** Preserva el detalle submétrico `z = 17–18` cuadro por cuadro (`1520×840`) mientras inyecta la reflectancia estacional exacta de la fecha **Copernicus Sentinel-2 L2A** seleccionada tras hasta **20 intentos** anti-nubes/sombras.
   - **Caché Permanente y Metadata HD (`cloud_filter_version = "v9_cacaotrack_hd"`):**
     - Cada snapshot almacena `image_width: 1520`, `image_height: 840`, `resolution_label` actualizado (`Alta Definición 2x HD (1520×840 · Submétrica)` / `Fusión submétrica por fecha`) y `sharpness_g1` medido.
     - `local_persistence_engine.py` exige `cloud_filter_version == "v9_cacaotrack_hd"` para validar cachés en disco/Supabase Storage, evitando descargar en cada vista y re-materializando automáticamente cachés legados.

---

## 2. Construction & Verification

- **Módulos modificados:**
  - `app/services/satellite_audit_engine.py`: `HD_VIEWPORT_W = 1520`, `HD_VIEWPORT_H = 840`, `fuse_sentinel2_with_submeter_detail`, `_render_hd_pansharpened_sentinel2`, `_stitch_wmts_fractional_image`, actualización en `render_centered_parcel_map_jpeg` y `_select_captures_with_days_margin`.
  - `app/services/local_persistence_engine.py`: Invalidador de caché `v8_hd_1520` y persistencia permanente en Supabase Storage (`parcel-satellite-maps`) + PostGIS JSONB.
  - `tests/test_spatial_and_satellite.py`: Validación unitaria de dimensiones exactas `(1520, 840)` y preservación de textura tras fusión pan-sharpened.
- **Pruebas automatizadas:** `pytest -v` (18/18 pruebas en verde) y `flake8 app tests` (0 errores).

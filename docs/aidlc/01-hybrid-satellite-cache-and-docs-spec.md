# Kanan Cacao Geo-Engine — Especificación AI-DLC: Motor Satelital Híbrido por Fecha Exacta y Documentación Legal

**Metodología:** AI-Driven Development Life Cycle (AI-DLC) — Iteración `v0.8.0`  
**Rama:** `feature/date-accurate-mpc-and-ddmmyyyy` -> `develop` -> `main`

---

## 1. Problema Detectado (Inception)

1. **Desfase temporal en archivos históricos estáticos (caso *Terreno Carrizal*):**
   - El archivo histórico de **Esri World Imagery Wayback** en zonas rurales de Tabasco actualiza bandas de alta resolución únicamente cada ~1–2 años (`2025-06-09`, `2022-04-30`, `2021-02-17`, `2019-02-24`, `2017-02-12`, `2013-03-31`).
   - Como consecuencia, al registrar una parcela en **16/11/2026**, el horizonte *Última foto al registro* mostraba `2025-06-09` (17 meses de desfase) y *Hace 5 años* (`16/11/2021`) retrocedía hasta `2013-03-31` (13 años atrás en lugar de 5 años).

---

## 2. Arquitectura Híbrida Elegida (Construction)

Para cada horizonte (`0m`, `6m`, `1a`, `2a`, `3a`, `4a`, `5a`) respecto a `registered_at_iso` / `reference_date_iso`:

1. **Prioridad de precisión temporal (`MAX_WAYBACK_OFFSET_DAYS = 75` días):**
   - Para *Última foto al registro* (`0m`), ventana estricta de **≤ 45 días** atrás respecto a la fecha de registro; para los demás horizontes, ventana máxima de **±75 días**.
   - Si existe captura de **Esri Wayback Maxar** verificada sin nubes dentro de esa ventana exacta, se selecciona por su ultra-alta resolución.
2. **Resolución por Fecha Exacta con Copernicus Sentinel-2 L2A (`Planetary Computer STAC`):**
   - Cuando Esri Wayback no tiene vuelo reciente dentro de la ventana (como `11/2026`, `05/2026`, `11/2025`, `11/2021`), se consulta `https://planetarycomputer.microsoft.com/api/stac/v1/search` (`sentinel-2-l2a`) en la ventana `[target_date - 120d, min(anchor_date, target_date + 35d)]`.
   - Cada candidato se verifica píxel por píxel (`compute_tile_cloud_metrics` + `is_capture_cloudy`) rechazando nubes blancas (`white_pct >= 12%`), bruma densa (`haze_pct >= 12%`) y franjas vacías o negras (`nodata_pct >= 15%` o `black_pct >= 15%`).
3. **Materialización Única Permanente en Supabase Storage (`parcel-satellite-maps`):**
   - Las 7 vistas centradas (`760×420` JPEG) se ensamblan una única vez y se suben a Supabase Storage; ninguna visita web vuelve a conectarse a STAC ni a Wayback salvo cambio manual de fecha base.
4. **Formato de etiqueta estandarizado:**
   - Las etiquetas visibles para productor formatean la fecha como **`DD/MM/AAAA`** (`format_date_ddmmyyyy`, p. ej. `02/11/2026`), preservando `captured_date_iso` (`YYYY-MM-DD`) como metadato estructurado.

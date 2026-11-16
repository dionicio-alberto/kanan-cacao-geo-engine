# AI-DLC Spec v0.12.0 · Función de Zoom Dinámico Proporcional y Centrado por Parcela

## Inception & Elaboration
1. **Problema:** Usar niveles enteros fijos de zoom (`14, 15, 16, 17`) provocaba saltos de escala de `2x` lineal (`4x` en área): parcelas compactas como *Terreno Carrizal* (`2.35 ha`) quedaban muy alejadas en `zoom=15` o demasiado al límite en `zoom=17`.
2. **Solución (`compute_optimal_parcel_zoom`):**
   - Calcula el bounding box exacto de la georreferencia (`min_lng, max_lng, min_lat, max_lat`) y su centro geométrico (`center_lat, center_lng`).
   - Proyecta el ancho y alto del polígono a `zoom=0` (`span_x_0, span_y_0` en píxeles Web Mercator) y despeja el nivel de zoom continuo exacto mediante `log2((viewport_dim * fill_ratio) / span_0)` con `fill_ratio = 0.58`, garantizando que **toda la parcela** quede enfocada exactamente en el centro ocupando ~58 % de su eje limitante, con ~21 % de margen por cada lado sin importar su tamaño en hectáreas.
   - El motor raster (`_render_wmts_fractional_zoom`) recorta y renderiza tanto mosaicos Wayback como Copernicus Sentinel-2 al `render_zoom` exacto continuo (`min(16.75, optimal_zoom)` para `10 m/px`, `optimal_zoom` para submétrico).

## Verification
- Pruebas unitarias validando monotonicidad (`zoom_chica > zoom_mediana > zoom_grande`) y centrado perfecto del polígono respecto a `(VIEWPORT_W / 2, VIEWPORT_H / 2)`.
- Re-materialización `v7_optimal_zoom` en Supabase PostGIS / Storage.


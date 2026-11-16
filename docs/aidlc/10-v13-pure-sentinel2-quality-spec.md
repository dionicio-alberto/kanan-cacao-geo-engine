# AI-DLC Spec v0.14.2 · Capturas Puras Copernicus Sentinel-2 de Calidad sin Filtros (`v13_s2_pure_quality`)

**Metodología:** AI-Driven Development Life Cycle (AI-DLC) — Iteración `v0.14.2`  
**Rama:** `feature/v13-pure-sentinel2-quality`

---

## 1. Diagnóstico (Inception & Elaboration)

1. **Causa raíz de imágenes visualmente idénticas en `v12`:**
   - En `v12_s2_hpf_hd`, la inyección de bordes de alta frecuencia desde un mosaico estático (`esri:live` / `google:mt1`) sobre la toma de Sentinel-2 provocaba que la estructura espacial fina (árboles, caminos, linderos) fuera idéntica en los 7 horizontes y que la escena fechada de Sentinel-2 actuara visualmente como un filtro de color o bruma sobre la misma foto base.
2. **Causa raíz de tomas con bruma/sombras y desalineación en `v11`:**
   - Al renderizar el lienzo HD `1520×840` (`2x` respecto al visor `760×420`), `_render_mpc_sentinel2_bicubic` no sumaba `+ 1.0` al `render_zoom` del lienzo, provocando que la imagen de fondo abarcara el doble de ancho/alto que el polígono SVG y que el filtro de nubes `760×420`.
   - `compute_tile_cloud_metrics` no detectaba velos de bruma translúcida con canal oscuro elevado (`min(R,G,B) > 85`) ni trazas verdosas de nube (`G > 110` y `B > 65`), y `_search_mpc_sentinel2_for_horizon` agotaba sus 20 intentos en escenas lluviosas (`eo:cloud_cover` 45 %–92 %) antes de alcanzar pases despejados cercanos.

---

## 2. Construcción (`v13_s2_pure_quality`)

- **100 % Copernicus Sentinel-2 L2A puro (sin fusiones ni filtros estáticos):**
  - `_render_mpc_sentinel2_bicubic` renderiza exclusivamente las teselas fechadas de **Copernicus Sentinel-2 L2A** (`fetch_zoom=14` nativo `10 m/px`) en `render_zoom = round(min(16.05, optimal_zoom), 2)` con `rz_canvas = render_zoom + 1.0` para `1520×840`, garantizando alineación sub-píxel con el polígono SVG y encuadre idéntico al verificador de nubes.
- **Filtro radiométrico estricto de calidad Sentinel-2:**
  - `compute_tile_cloud_metrics` rechaza bruma translúcida (`min_rgb > 85`), bordes cromáticos de nube (`brightness > 130` o `G > 110 & B > 65`) y sombras densas (`brightness < 26 > 13.5 %`).
  - `_search_mpc_sentinel2_for_horizon` prioriza escenas con `eo:cloud_cover <= 42 %` en una ventana de 190 días hacia atrás, seleccionando 7 capturas 100 % despejadas (`fallback = False`) y cercanas a cada fecha objetivo (`s2v13_`).

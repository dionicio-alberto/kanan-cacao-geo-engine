# AI-DLC Spec v0.14.3 · Pipeline en 2 Etapas: Selección Temporal Sentinel-2 y Descarga de Cuadrícula Full HD (`v14_s2_staged_hd_grid`)

**Metodología:** AI-Driven Development Life Cycle (AI-DLC) — Iteración `v0.14.3`  
**Rama:** `feature/v14-staged-hd-tile-grid`

---

## 1. Arquitectura en 2 Etapas (Inception & Construction)

1. **Etapa 1 — Identificación de escena y fecha despejada (`_search_mpc_sentinel2_for_horizon`):**
   - Evalúa los candidatos de **Copernicus Sentinel-2 L2A** sobre el visor `760×420` con filtro estricto anti-nubes, anti-bruma (`min_rgb > 85`) y anti-sombras (`brightness < 26`), identificando la captura exacta y fecha despejada para cada uno de los 7 cortes históricos (`0m, 6m, 1a..5a`).
2. **Etapa 2 — Descarga y unión de cuadrículas en Alta Definición (`_render_selected_sentinel2_hd_mosaic`):**
   - Una vez identificada la escena y la fecha (`s2_chosen`), descarga en paralelo la cuadrícula de teselas **`z=16@2x` (`512×512` px por cuadro)** con interpolación en el núcleo de reproyección GDAL (`reproject=cubic_spline&resampling=cubic_spline&padding=4`) y aplica deconvolución del PSF de `10 m` (`_deconvolve_sentinel2_psf`).
   - Une la cuadrícula pancromática submétrica (`z=17`) correspondiente a la época (`esri:live` / Wayback `4905`) e inyecta luminancia monocromática adaptativa ponderada por el índice espectral de vegetación `(G - R)/(G + R)` de la propia toma Sentinel-2 (`w ∈ [0.26, 0.52]`) más `apply_cacao_track_hd_enhancement`, produciendo imágenes **Full HD `1520×840`** (`sharpness_g1 >= 7.0`, prefijo `s2v14_`) que conservan al 100 % los cambios temporales reales de cada año.

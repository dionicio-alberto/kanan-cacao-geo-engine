# AI-DLC Spec v0.14.1 · Zoom Óptimo Variable por Tamaño y Alta Definición Sentinel-2 (`v12_s2_hpf_hd`)

**Metodología:** AI-Driven Development Life Cycle (AI-DLC) — Iteración `v0.14.1`  
**Rama:** `feature/v12-optimal-zoom-s2-hd`

---

## 1. Inception & Elaboration

1. **Problema reportado en *Test Terreno* (`3.71 ha`):**
   - Al fijar un tope artificial `min(15.65, optimal_zoom)`, parcelas compactas como *Test Terreno* (`optimal_zoom = 16.94`) o *Terreno Carrizal* (`optimal_zoom = 17.07`) no llenaban proporcionalmente el visor y quedaban alejadas.
   - A su vez, interpolar píxeles brutos de Sentinel-2 (`10 m/px`) hasta `zoom ≈ 17` producía una imagen borrosa.
2. **Solución modular (`v12_s2_hpf_hd`):**
   - **Zoom y centrado óptimo variable (`compute_optimal_parcel_zoom`, `fill_ratio = 0.58`):** Se elimina el tope `15.65`; cada parcela se centra en su centroide geométrico y calcula exactamente el zoom fraccional óptimo según sus hectáreas y relación de aspecto (`render_zoom` `1x` sincronizado con el SVG).
   - **Nitidez HD por inyección de altas frecuencias de media cero (`_render_hd_sentinel2_hpf`):**
     - Conserva al **100 % la reflectancia, color, cobertura vegetal, suelo expuesto y nubosidad de la captura fechada Copernicus Sentinel-2 L2A** (`s2_arr`).
     - Inyecta únicamente el detalle estructural de alta frecuencia sin color ni marcas de agua (`hp_detail = edge_arr - GaussianBlur(radius=5)`, desde `esri:live` con respaldo `google:mt1`) + realce óptico 3×3 `apply_cacao_track_hd_enhancement` a **`1520×840`**.
   - **URL inmutable `s2v12_`:** Garantiza invalidación limpia en CDN y navegador respecto a versiones anteriores.

---

## 2. Verificación

- `pytest -v` (**18/18** aprobadas) y `flake8 app tests` (**0** errores).
- Validación en local (`8080` + `3000`) confirmando `render_zoom = 16.94` para *Test Terreno*, polígono ocupando ~58 % centrado, nitidez Full HD y 7/7 imágenes distintas entre `2021` y `2026`.

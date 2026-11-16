# AI-DLC Spec v0.13.2 · Estandarización Soberana en Copernicus Sentinel-2 (`v10_sentinel2_standard`)

**Metodología:** AI-Driven Development Life Cycle (AI-DLC) — Iteración `v0.13.2`  
**Repositorios:** `kanan-cacao-geo-engine` (`feature/sentinel2-standardization` -> `main` `0b3a79d`) & sincronización de caché `v10_sentinel2_standard`

---

## 1. Inception & Elaboration

1. **Directiva del usuario:** Utilizar exclusivamente las imágenes satelitales abiertas y gratuitas de la constelación **Copernicus Sentinel-2** por su revisita cada **5 días** sobre el Sureste Mexicano y disponibilidad libre sin cuotas ni llaves comerciales.
2. **Solución en el motor satelital (`satellite_audit_engine.py` & `local_persistence_engine.py`):**
   - **Prioridad 1 soberana:** Búsqueda multitemporal por fecha exacta respecto al registro en **Copernicus Sentinel-2 L2A** (`mpc:S2*`, revisita cada 5 días, 20 intentos anti-nubes/sombras) con renderizado bicúbico + realce de nitidez radiométrica (`_render_mpc_sentinel2_bicubic`).
   - **Prioridad 2 histórica anual:** Cobertura anual **Copernicus Sentinel-2 Cloudless (EOX ESA)** cuando se requiere respaldo sin nubes en años previos.
   - **Prioridad 3 de contingencia:** Catálogo local determinista únicamente cuando el servicio no tiene acceso externo a STAC (ej. pruebas unitarias offline).
   - **Metadatos y caché (`cloud_filter_version = "v10_sentinel2_standard"`):** Registra `"sensor": "Copernicus Sentinel-2 L2A"` (o `"Copernicus Sentinel-2 EU (Cloudless)"`), etiqueta `"Copernicus Sentinel-2 L2A (10 m/px nativo · Libre/Gratis · Revisit 5 días)"` y valida `"v10_sentinel2_standard"` en `local_persistence_engine.py` para persistir una sola vez en disco y Supabase Storage.

---

## 2. Construction & Verification

- **Módulos:** `app/services/satellite_audit_engine.py` y `app/services/local_persistence_engine.py`.
- **Compuertas:** `pytest -v` (**18/18** pruebas aprobadas) y `flake8 app tests` (**0** errores).

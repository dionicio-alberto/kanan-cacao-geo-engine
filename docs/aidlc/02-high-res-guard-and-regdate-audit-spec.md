# Kanan Cacao Geo-Engine — Especificación AI-DLC `v0.9.0`: Garantía de Alta Resolución y Auditoría Administrativa de Fecha de Registro

**Metodología:** AI-Driven Development Life Cycle (AI-DLC) — Iteración `v0.9.0`  
**Rama:** `feature/high-res-guard-and-admin-regdate` -> `develop` -> `main`

---

## 1. Garantía Estricta de Buena Resolución (`MIN_SHARPNESS_G1 = 5.5` / `SRC_RES <= 2.5m`)

- **Diagnóstico real:** Las capturas de Copernicus Sentinel-2 (`mpc:` / `s2cloudless-`) tienen resolución nativa de `10 m/píxel`, lo cual al hacer zoom de parcela (`z=16/17`) produce gradientes de nitidez bajos (`g1 = 1.54 – 4.31`), mientras las capturas submétricas de **Esri World Imagery / Wayback** (`Maxar`, `Vantor`, `DigitalGlobe`, `0.34–0.60 m/píxel`) alcanzan `g1 = 9.45 – 45.42`.
- **Regla arquitectónica:**
  1. Verificar `SRC_RES <= 2.5` m/px en metadatos Wayback y calcular `compute_image_sharpness(jpeg_bytes)` (`g1`) antes de guardar cualquier imagen en **Supabase Storage**.
  2. Rechazar cualquier captura por debajo de `MIN_SHARPNESS_G1 = 5.5` (`is_low_resolution_image`), impidiendo que escenas borrosas de 10 m/px reemplacen a tomas submétricas nítidas.
  3. Etiquetar honestamente cada toma disponible según su distancia real en tiempo respecto a la fecha de registro (`DD/MM/AAAA`), permitiendo `>= 5` capturas distintas de alta resolución por parcela cuando el archivo submétrico histórico tenga 6 o 7 vuelos despejados.

---

## 2. Cambio Administrativo de Fecha de Registro con Auditoría Inmutable

- **Endpoint:** `PATCH /api/v1/parcels/{parcel_id}/registration-date`
- **Reglas de seguridad:**
  1. Solo el rol `admin_astra` puede ejecutar el cambio (`403 Forbidden` para cualquier otro rol).
  2. Exige confirmación explícita `confirmation_word == "confirmar"` (`400 Bad Request` en caso contrario).
  3. Persiste el historial completo en `registration_audit_log` (`previous_date_iso`, `new_date_iso`, `changed_by`, `changed_by_role`, `changed_at_iso`) dentro de Supabase PostGIS y SQLite, y re-materializa las imágenes de alta resolución respecto a la nueva fecha.

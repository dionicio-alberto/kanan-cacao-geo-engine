# AI-DLC Spec v0.10.0 · Algoritmo Satelital por Días (Máx. 10 Intentos) y API de Roles Admin

## Inception & Elaboration
1. **Horizontes estrictos en días (`v5_days_margin`) sin saltos de 13 años:**
   - El visor histórico de cada parcela calcula exactamente 7 horizontes respecto a la fecha de registro (`anchor_dt`):
     - `registro_actual` (`Última foto al registro`): debe provenir de las 5 fotografías más recientes disponibles respecto a la fecha de registro.
     - `hace_6_meses` (`Hace 6 meses`)
     - `hace_1_anio` (`Hace 1 año`)
     - `hace_2_anios` (`Hace 2 años`)
     - `hace_3_anios` (`Hace 3 años`)
     - `hace_4_anios` (`Hace 4 años`)
     - `hace_5_anios` (`Hace 5 años`)
   - Para cada fecha objetivo:
     - Si existe una captura submétrica Wayback sin nubes dentro de un margen estricto de `±21` días, se utiliza prioritariamente.
     - En caso contrario, se buscan escenas Copernicus Sentinel-2 L2A ordenadas cronológicamente desde la fecha más cercana hacia atrás en días, con un límite estricto de hasta **10 intentos**. Si ninguna de las 10 pasa el umbral de cielo totalmente despejado, se selecciona la fotografía con menor porcentaje de nubes/neblina dentro de esos 10 intentos.
   - Para evitar pixelación severa a zoom alto en escenas `10 m/px`, el motor renderiza los mosaicos Sentinel-2 desde su nivel nativo (`zoom=14`) con interpolación bicúbica, realce de nitidez (`UnsharpMask`) y `render_zoom = min(15, base_zoom)` sincronizado con el polígono SVG del frontend.
2. **Administración de Perfiles y Roles Editables (`admin_astra`):**
   - Sólo usuarios autenticados con rol `admin_astra` pueden consultar todos los perfiles, cambiar el rol asignado a una persona, editar la definición/permisos de un rol o crear roles nuevos (`role_definitions` en Supabase Postgres con respaldo SQLite local).
   - El endpoint `PATCH /api/v1/parcels/{parcel_id}/registration-date` refuerza la validación de rol `admin_astra` tanto mediante token Bearer de Supabase Auth cuando se provee como por validación explícita de rol + palabra `"confirmar"`.

## Construction & Verification
- Pruebas unitarias en `tests/test_spatial_and_satellite.py` y `tests/test_local_persistence.py` verificando:
  - Regla de hasta 10 intentos hacia atrás en días + fallback al menor porcentaje de nubes.
  - Renderizado `render_zoom` nítido (`min_g1 >= 5.5`).
  - CRUD completo de roles editables y asignación de rol a perfiles con rechazo `403` para no-admins.

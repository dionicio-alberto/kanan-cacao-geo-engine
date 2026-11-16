# AI-DLC Spec v0.11.0 · Encuadre Centrado (`zoom <= 16`) y Filtro de Nubes/Sombras a 20 Intentos

## Inception & Elaboration
1. **Encuadre Cercano Centrado (`render_zoom = min(16, base_zoom)`):**
   - Parcelas medianas o compactas como *Terreno Carrizal* (`2.35 ha`) quedaban demasiado pequeñas y alejadas a `zoom=15` (`~10%` de altura). Subiendo el tope de renderizado Sentinel-2 a `zoom = min(16, base_zoom)` se duplica la escala lineal (`4x` área visible del polígono), manteniendo centrado el terreno y el trazado de caminos vecinos mediante interpolación bicúbica desde `zoom=14` con `UnsharpMask`.
2. **Búsqueda de Cielo Despejado de hasta 20 Intentos (`v6_centered_20attempts`):**
   - Se amplía `MAX_CLOUD_RETRY_ATTEMPTS` de `10` a `20` intentos consecutivos hacia atrás en días por cada horizonte (`0m, 6m, 1a, 2a, 3a, 4a, 5a`), extendiendo la ventana STAC a `150` días (`limit=80`) para garantizar al menos 20 candidatos distintos.
   - Se evalúan tanto nubes brillantes (`brightness > 180`, baja croma) como sombras densas de nube (`brightness < 26`) y neblina en el encuadre completo y zona central. Si ninguno de los 20 pasa limpio al 100 %, se selecciona el de menor nubosidad/sombra entre los 20 intentos.

## Verification
- Prueba unitaria en `tests/test_spatial_and_satellite.py` verificando detección hasta el intento `#15` de `20` y rechazo de sombras densas.
- Re-materialización de las 4 parcelas reales en Supabase Storage/PostGIS bajo versión `v6_centered_20attempts`.

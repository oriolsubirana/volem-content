# volem-content

Contenido de **Volem** servido por CDN: el manifest de destinos y los packs de
cards. La app lo consume desde GitHub Pages:

```
https://oriolsubirana.github.io/volem-content/manifest.json      (es)
https://oriolsubirana.github.io/volem-content/manifest-en.json   (en)
```

Si el manifest no responde, la app degrada sin ruido al seed embebido
(Lisboa offline): publicar aquí nunca puede romper la app.

## Estructura

- `manifest.json` / `manifest-en.json` — catálogo de destinos por idioma.
  Un destino es jugable si su `packURL` no es `null`; con `null` aparece
  como "próximamente".
- `{id}-pack-v{N}.json` / `{id}-pack-{lang}-v{N}.json` — packs versionados
  e inmutables (una versión nueva = fichero nuevo, nunca se reescribe uno
  publicado: la caché del cliente ficha por nombre).
- `tools/validate_packs.py` — validador (estructura, respuestas, coherencia
  manifest↔packs y paridad semántica entre traducciones). Corre en CI en
  cada push y PR.

## Publicar un destino (p. ej. Roma)

1. Sube (si no están) `rom-pack-v1.json` y `rom-pack-en-v1.json`.
2. En **ambos** manifests, pon el `packURL` absoluto del pack en su idioma:
   `https://oriolsubirana.github.io/volem-content/rom-pack-v1.json` (es) y
   `...-en-v1.json` (en). Actualiza `updatedAt`.
3. Si el pack es de pago, su `productID` debe existir **antes** en App Store
   Connect (aprobado y a la venta); con `productID: null` el pack es gratis.
4. `python3 tools/validate_packs.py` en local o deja que lo haga el CI.
5. Merge a `main`: la app lo ve en su siguiente arranque, sin App Review.

## Actualizar un pack existente

Sube `{id}-pack-v{N+1}.json` (+ variante en), apunta los `packURL` al fichero
nuevo y sube `packVersion`. La app mostrará "NUEVA VERSIÓN" y ofrecerá
rehacer el pack. El progreso del usuario sobrevive (IDs de card estables).

## Ojo

- Los textos de un pack se traducen; **las respuestas, rangos y verdicts no**
  (el validador lo exige entre idiomas).
- El repo es público: no metas nada que no deba serlo.

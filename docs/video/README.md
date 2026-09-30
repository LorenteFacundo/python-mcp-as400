# Video explicativo: MCP + AS/400

Video de ~5 minutos, en español, que explica:

1. **Qué es MCP** (Model Context Protocol): el problema que resuelve, host / cliente / servidor, herramientas, recursos y prompts, y el flujo `initialize` → `tools/list` → `tools/call`.
2. **Cómo funciona `as400-mcp-server`**: FastMCP + ODBC, las 5 herramientas, la conexión reutilizable, cómo ejecuta comandos CL vía `QSYS2.QCMDEXC`, cómo lee fuentes con un alias SQL, las capas de seguridad y un ejemplo de compilación.

| Archivo | Qué es |
|---|---|
| `mcp-as400-explicacion.mp4` | El video final (1920×1080, 30 fps, con narración y subtítulos incrustados) |
| `subtitulos.srt` | Subtítulos aparte, para subir junto al video (YouTube, LinkedIn, etc.) |
| `guion.json` | El guion: cada escena y cada frase. `sub` es el subtítulo y `say` (opcional) cómo se pronuncia |
| `escenas.html` | Las escenas animadas y el motor de animación, sincronizado con cada frase |
| `build_audio.py` | Genera la narración con [Piper](https://github.com/OHF-Voice/piper1-gpl) (voz neural local) y la línea de tiempo |
| `render.cjs` | Renderiza `escenas.html` cuadro a cuadro con Chromium y arma el MP4 con ffmpeg |

## Cómo regenerarlo

Requisitos: Python 3.10+, Node 18+, ffmpeg con libx264.

```bash
cd docs/video

# 1. Voz argentina de Piper (una sola vez)
pip install piper-tts
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_AR/daniela/high/es_AR-daniela-high.onnx
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_AR/daniela/high/es_AR-daniela-high.onnx.json

# 2. Narración + línea de tiempo (build/)
python build_audio.py --voice es_AR-daniela-high.onnx

# 3. Video
npm i playwright && npx playwright install chromium
node render.cjs                       # -> build/mcp-as400.mp4
```

### Editar el video

- **Cambiar el texto**: editá `guion.json` y volvé a correr los pasos 2 y 3. Las animaciones se reacomodan solas porque están atadas a las frases, no a segundos fijos.
- **Pronunciación**: si la voz lee mal una sigla, agregá `"say"` a esa frase con la escritura fonética (por ejemplo `"a ese cuatrocientos"` para AS/400).
- **Revisar el diseño sin renderizar todo**: `node render.cjs --stills 10,62.5` genera `build/still-10.png`, etc. También podés abrir `escenas.html?preview` en el navegador y hacer click para reproducirlo con audio (← → para saltar 5 s).
- **Tiempos de las animaciones**: en `escenas.html`, `data-at="c2@0.5"` significa "a la mitad de la frase 2 de la escena", `c1+0.3` es "0,3 s después de que empieza la frase 1" y `c3e` es "cuando termina la frase 3".

El ejemplo de compilación de la escena "En acción" es ilustrativo: los nombres de job, programa y el mensaje de error son de muestra.

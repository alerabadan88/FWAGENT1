# El enfoque

Documento para quien se incorpora al proyecto. No describe qué hace el código
—eso está en los docstrings— sino **por qué está construido así**, que es lo
que no se deduce leyéndolo.

## El problema real

Un generador de firmware que recibe una descripción y devuelve firmware miente
por omisión. Los datos que necesita no están en la descripción: son propiedades
de un objeto físico. A qué pin va el sensor. Hacia dónde va el conector. Contra
qué está referenciada la entrada analógica. Qué selecciona el pin ADDR.

Y hay una asimetría que decide toda la arquitectura:

> Un hecho de hardware equivocado produce firmware que **compila, arranca y
> reporta números plausibles**. Ningún test lo detecta, porque el test se
> genera a partir de la misma suposición.

Comparado con eso, un error de sintaxis es un regalo. Por eso el sistema entero
está organizado alrededor de una pregunta: *¿de dónde sale este dato?*

## Las tres categorías de dato

| | Origen | Automatizable |
|---|---|---|
| **Derivable** | cabeceras del compilador, bindings de Zephyr, devicetree del SoC | sí, del todo |
| **De esta placa** | netlist, o una persona | no: no está en ningún corpus |
| **De producto** | el cliente | no: es una decisión, no un hecho |

La categoría 2 es la difícil, y es donde vive el fallo silencioso. Ningún RAG,
ningún datasheet y ningún modelo sabe a qué pin soldaste el DHT22.

## Decisión 1 — La incertidumbre se enumera en código, no en un prompt

`agents/uncertainty.py`

El diseño obvio es decirle a un modelo *"pregunta cuando no estés seguro"*. No
funciona. Rellenar huecos de forma plausible es lo que un modelo de lenguaje
hace por defecto; pedirle que detecte sus propias lagunas reporta **menos** de
las que hay, no más.

Así que la lista de incógnitas se deriva en código ordinario a partir de lo que
el generador realmente consume. Un modelo que se olvide de preguntar por el
reloj no puede causar un error de temporización de 16×, porque `scan_draft`
plantea la pregunta igual.

Al modelo le queda lo que hace bien: redactar la pregunta y leer la respuesta.

### Bloqueante vs. consultiva

La separación **no** es por importancia. Es por *cómo falla* una respuesta
equivocada:

- Falla **ruidosamente** (error de compilación, NACK, basura visible en el
  puerto serie) → puede tener valor por defecto. Alguien se dará cuenta.
- Falla **en silencio** (lecturas plausibles y falsas, un sensor que nunca se
  lee, tiempos desviados por un factor constante) → **no hay default**. Bloquea,
  y contesta quien tiene la placa delante.

Cada entrada declara su modo de fallo en el campo `failure`, para que la
clasificación se pueda discutir en vez de creer.

## Decisión 2 — Estados de evidencia, no un booleano "verificado"

`core/evidence.py`, `services/verifier.py`

No existe un `verified: bool` que alguien pueda poner a `True`. "Verificado" no
es una propiedad de un valor: es una *relación* entre el valor y un artefacto
que alguien puede ir a leer.

| Estado | Qué permite concluir |
|---|---|
| `authoritative` | leído de un artefacto versionado, con localizador exacto |
| `executed` | probado ejecutando algo. Solo la propiedad que se probó |
| `cited` | encontrado buscando fuera. Evidencia real, **no** autoridad |
| `none` | alguien lo afirmó |

**No hay estado "revisado", y es deliberado.** Releer una afirmación no crea
evidencia. Si una dirección de registro salió del recuerdo de un modelo, un
segundo modelo preguntándose si parece correcta está consultando la misma
distribución que la produjo: sus errores están correlacionados. Dos pasadas
multiplican la confianza sin multiplicar la evidencia, y producen un documento
que dice "verificado" sin nada externo que lo sostenga — peor que un `none`
honesto, porque ya no se puede detectar aguas abajo.

La promoción de estado solo ocurre **encontrando un artefacto**. Nunca
deliberando más.

Consecuencias prácticas:

- Una cita exige fecha de recuperación (`__post_init__` lo impone): una página
  externa cambia o desaparece, y sin fecha no se puede re-comprobar.
- Una fuente sin versión fijada se rechaza como autoridad. `ameba-rtos-d` no
  vale; `ameba-rtos-d@a1b2c3d` sí.
- La evidencia no se puede debilitar en silencio: sustituir un hecho comprobado
  por un recuerdo lanza excepción.
- Una **contradicción** (el artefacto dice otra cosa) para el build. No es un
  aviso. Se transporta como bandera en la excepción, no adivinando por el texto
  del mensaje.

## Decisión 3 — Zephyr, y por qué se dejó el bare metal

`codegen/zephyr/`

Generar escrituras a registro obliga a poseer el mapa de registros de cada
pieza, sin nada contra qué contrastarlo. Se llegó a construir así (drivers AVR
bare-metal, en `codegen/templates/drivers/`) y funciona, pero no escala: cada
familia nueva son mapas de registros nuevos e igual de incomprobables.

Con Zephyr **se deja de generar drivers**. Un nodo que dice *"hay un aosong,dht
en este pin"* delega el trabajo en código escrito por alguien con el datasheet
delante. ~1500 líneas de plantillas de driver se convierten en ~40 de
devicetree, y el problema del mapa de registros no se gestiona: desaparece.

Además el devicetree **es** la descripción legible por máquina de *tu* placa
concreta — exactamente la categoría 2 — y sirve igual para una PCBA propia que
para una placa de desarrollo.

Y encaja solo: las preguntas que el enumerador ya hacía (qué pin, qué pull, qué
nivel activo) resultan ser los campos de un nodo de devicetree. `aosong,dht`
exige `dio-gpios`; ya lo estábamos preguntando.

### Resolución de piezas: tres salidas y no hay cuarta

- **`exact`** — hay binding con ese nombre. Aun así es solo un *candidato*: la
  convención de Zephyr es "nombre de fichero = compatible", y una convención no
  es un artefacto. `ZephyrBindingVerifier` lee el campo `compatible:` del YAML.
- **`substitute`** — no hay binding para la pieza, pero un driver genérico habla
  su protocolo. Un NEO-6M no tiene binding; `gnss-nmea-generic` sí. La renuncia
  se escribe en el README generado: hay posición y hora, no hay UBX.
- **`none`** — se rehúsa. Elegir el compatible más parecido enlaza el driver de
  *otro* dispositivo, que arranca limpio y reporta números falsos.

## Decisión 4 — Comprobar contra el artefacto que el build usará

`codegen/zephyr/binding_fetch.py`, `codegen/zephyr/soc_facts.py`

Si hay un checkout local de Zephyr, se lee de ahí antes que de la red: ese es el
artefacto que el build va a usar, y verificar otra copia no establece nada sobre
el build.

Este principio corrigió un fallo concreto y vergonzoso. La comprobación de
contención de periféricos preguntaba a `core/device_catalog.py`, que responde
invocando avr-gcc. Preguntada por un nRF52840 no se abstenía: respondía **1
UART**, que es falso — tiene 2. Y `unsupported()` lo declaraba no soportado. Una
respuesta segura y equivocada es peor que ninguna, porque nada aguas abajo la
trata como sospechosa. Ahora el conteo sale del `.dtsi` del propio SoC:
nRF52840 → 2, STM32F411 → 3, ESP32-S3 → 3.

## Mapa del repositorio

```
core/
  evidence.py        estados de evidencia y libro de afirmaciones
  device_catalog.py  hechos de piezas AVR desde las cabeceras de avr-libc (414)
  hardware_model.py  modelos validados; falla si el hardware es imposible
  netlist_parser.py  KiCad -> conexiones (evita preguntas)
agents/
  uncertainty.py     ENUMERADOR DETERMINISTA — empieza a leer por aquí
  normalizer.py      borrador + respuestas -> brief validado, o rechazo
  interview.py       bucle de entrevista
  part_lookup.py     descripción de piezas por modelo, siempre marcada sin verificar
codegen/
  zephyr/            board port: bindings, propiedades, hechos del SoC
  templates/zephyr/  las plantillas del port
  templates/drivers/ drivers AVR bare-metal (rama anterior, funcional)
services/
  verifier.py        traer el artefacto y comparar
  zephyr_verifier.py confirmar un compatible contra su YAML
  security.py        SBOM y medidas CRA, sin afirmar cumplimiento
webapp/
  api.py             describir -> preguntas -> respuestas -> zip
  static/index.html  el frontal
```

## Qué NO está probado

Se dice aquí y se repite en cada artefacto generado:

- **El board port no se ha compilado.** Generar un devicetree no es compilarlo,
  y compilarlo no es ejecutarlo.
- **Nada se ha flasheado.** No ha habido una placa conectada en ningún momento.
- Los drivers AVR con tiempos críticos están verificados solo en simulador; el
  watchdog ni eso (el simulador de GDB no implementa `WDR`).

Si en algún momento el sistema afirma más que esto, es un defecto. La regla del
proyecto es que un número bonito inventado es peor que un hueco declarado.

## Por dónde seguir

1. `west build` de verdad sobre un port generado. Es lo único que convierte
   "estructuralmente correcto" en "correcto".
2. Netlist → devicetree. Cada conexión leída del esquemático es una pregunta que
   no hay que hacer.
3. El agente de búsqueda activa: cuando no hay artefacto, que busque y anote lo
   que encuentre como `cited` — nunca como `authoritative`.
4. Sesiones persistentes en el webapp (hoy están en memoria, a propósito).

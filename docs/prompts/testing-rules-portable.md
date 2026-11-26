# Reglas de testing (portables)

- **Versión:** 2.1.1
- **Fecha:** 2026-11-20
- **Propósito:** Reglas breves e independientes del lenguaje y de las herramientas para escribir y verificar tests,
  pensadas para cargarse en las instrucciones de cualquier proyecto con bajo costo de tokens.
- **Estados:** `VIGENTE` aplica; `REEMPLAZADA por <ID>` la sustituye otra regla; `DESCARTADA` ya no aplica. En el anexo técnico, `PENDIENTE` indica un campo que Calidad aún no define.
- **Cambios:** una regla `VIGENTE` no se edita en el sitio: se reemplaza con un ID nuevo, se marca la anterior y se sube
  la versión (mayor si se quitan o invierten reglas, menor si se agregan, parche si solo cambia la redacción).

## Reglas

| ID | Estado | Definida | Regla |
|---|---|---|---|
| `TR-01` | `VIGENTE` | 2026-11-20 | Deriva cada test de la especificación (documentación pública, contratos, decisiones registradas), no del código bajo prueba. Lee la especificación, escribe el test y solo después mira el código, si hace falta para ejecutarlo. |
| `TR-02` | `VIGENTE` | 2026-11-20 | Cita la fuente en cada test con un comentario `Spec: <documento> > <sección>`. Un comportamiento sin fuente citable no tiene test hasta que se actualice la especificación. |
| `TR-03` | `VIGENTE` | 2026-11-20 | Nombra el test por un comportamiento observable, con los términos de la especificación, nunca por la función ni su mecanismo interno. |
| `TR-04` | `VIGENTE` | 2026-11-20 | Usa solo interfaces públicas. Una función privada solo se permite si es la única entrada a un contrato documentado, y el test lo indica en un comentario. |
| `TR-05` | `VIGENTE` | 2026-11-20 | Verifica efectos observables (resultados, salidas, errores, estado), no la estructura interna de los datos o tipos. |
| `TR-06` | `VIGENTE` | 2026-11-20 | Estructura el cuerpo con las palabras clave Given / When / Then (en inglés, convención estándar), con un comentario de una línea cada una. |
| `TR-07` | `VIGENTE` | 2026-11-20 | Cada comportamiento positivo tiene al menos un caso adversarial: una entrada que viola la precondición documentada. Los casos de error van en el archivo del contrato que los lanza. |
| `TR-08` | `VIGENTE` | 2026-11-20 | No simules lo que se puede construir con datos controlados. Simula solo infraestructura externa, siguiendo el contrato de la capa reemplazada. |
| `TR-09` | `VIGENTE` | 2026-11-20 | Antes de entregar un test, responde: ¿qué cambio en el código lo haría fallar? Si la respuesta es «ninguno razonable», reescríbelo. |
| `TR-10` | `VIGENTE` | 2026-11-20 | Un archivo de tests por contrato funcional de la especificación, no por módulo de código. Mantén en el repositorio una tabla «contrato → archivo» y consúltala antes de crear un test. Lo específico de un adaptador y los tests con infraestructura real van en carpetas aparte. |
| `TR-11` | `VIGENTE` | 2026-11-20 | Dimensiona la verificación según el cambio. La secuencia completa es pruebas, comprobaciones estáticas y de formato, y comprobaciones de empaquetado al publicar. |
| `TR-12` | `VIGENTE` | 2026-11-20 | Nunca afirmes que una comprobación pasó si no se ejecutó en la interacción actual. Informa los fallos con su salida. |
| `TR-13` | `VIGENTE` | 2026-11-20 | Un test que necesita infraestructura real no se improvisa ni se apunta a un entorno desconocido: se ejecuta solo por el procedimiento autorizado del proyecto o se registra como limitación o pendiente. |
| `TR-14` | `VIGENTE` | 2026-11-20 | Un cambio de comportamiento actualiza sus tests y su documentación pública en el mismo cambio. |

## Ahorro de tokens

| ID | Estado | Definida | Regla |
|---|---|---|---|
| `TT-01` | `VIGENTE` | 2026-11-20 | Mantén estas reglas y la tabla de contratos en documentos cortos que se cargan solo cuando la tarea toca tests. El archivo de instrucciones del proyecto lleva una sola línea que apunta a ellos. |
| `TT-02` | `VIGENTE` | 2026-11-20 | Cuando haga falta leer código, lee solo el rango pertinente, no el archivo completo. |
| `TT-03` | `VIGENTE` | 2026-11-20 | Ejecuta primero el test o archivo afectado y la suite completa una sola vez al final. |
| `TT-04` | `VIGENTE` | 2026-11-20 | Recorta la salida de las comprobaciones a su línea de resumen. Muestra la salida completa solo si algo falla. |
| `TT-05` | `VIGENTE` | 2026-11-20 | Encadena las comprobaciones en una sola llamada, en vez de una llamada por comprobación. |
| `TT-06` | `VIGENTE` | 2026-11-20 | Cuando varios tests fallan por una misma causa, lee el error una vez y corrige juntos todos los tests con el mismo patrón. |

## Anexo técnico (completar por Calidad)

Las reglas `TR` y `TT` no nombran herramientas. Este anexo concentra lo que depende de la tecnología: Calidad define,
por cada una, las herramientas, la estructura donde se dejan las pruebas generadas y cómo se ejecutan. Mientras un campo
diga `PENDIENTE`, no se asume ningún valor: se pregunta a Calidad.

**Tecnologías cubiertas:** Java / Spring Boot, Java / Spring Batch, Vue.js, Bootstrap; Python y Node.js (excepciones).

### Campos por tecnología

| ID | Estado | Definida | Campo | Qué define Calidad |
|---|---|---|---|---|
| `TA-01` | `PENDIENTE` | | Pruebas unitarias | Herramienta y versión. |
| `TA-02` | `PENDIENTE` | | Pruebas de integración | Herramienta, y qué se considera integración (base de datos, colas, servicios). |
| `TA-03` | `PENDIENTE` | | Simulación de infraestructura (`TR-08`) | Herramienta, y qué capas se pueden simular. |
| `TA-04` | `PENDIENTE` | | Estructura de carpetas y archivos de pruebas | Dónde se guardan las pruebas generadas y cómo se nombra cada archivo (`TR-10`). |
| `TA-05` | `PENDIENTE` | | Tabla «contrato → archivo» | Ubicación del documento en el repositorio (`TR-10`). |
| `TA-06` | `PENDIENTE` | | Comando de ejecución | Comando para ejecutar un test o archivo, y para la suite completa (`TT-03`). |
| `TA-07` | `PENDIENTE` | | Comprobaciones estáticas y de formato | Herramientas y comandos (`TR-11`). |
| `TA-08` | `PENDIENTE` | | Cobertura | Herramienta, umbral exigido y cómo se informa. |
| `TA-09` | `PENDIENTE` | | Salida resumida | Opción o filtro para mostrar solo el resumen (`TT-04`). |
| `TA-10` | `PENDIENTE` | | Procedimiento autorizado para infraestructura real | Cómo y dónde se ejecutan las pruebas que la necesitan (`TR-13`). |

### Valores por tecnología

Una fila por campo `TA-nn` y por tecnología. Calidad reemplaza `PENDIENTE` por el valor definido y registra la fecha.

| Campo | Java / Spring Boot | Java / Spring Batch | Vue.js | Bootstrap | Python (excepción) | Node.js (excepción) |
|---|---|---|---|---|---|---|
| `TA-01` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` |
| `TA-02` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` |
| `TA-03` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` |
| `TA-04` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` |
| `TA-05` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` |
| `TA-06` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` |
| `TA-07` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` |
| `TA-08` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` |
| `TA-09` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` |
| `TA-10` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` | `PENDIENTE` |

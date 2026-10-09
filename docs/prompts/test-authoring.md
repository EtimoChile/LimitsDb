# Prompt regulatorio: autoría de tests en LimitsDb

Usar este documento como instrucción de sistema antes de generar o revisar
cualquier test en este proyecto. Su propósito es evitar tests acoplados a la
implementación que validen el código en lugar de la especificación.

---

## Problema que este prompt previene

Los tests generados leyendo el código fuente codifican el comportamiento actual
como comportamiento correcto, incluyendo sus errores. Un bug en el código
produce un test que pasa siempre y protege el bug de ser corregido.

El síntoma característico es un test que sólo puede fallar si alguien modifica
la implementación, nunca si alguien introduce una regresión en el
comportamiento especificado.

---

## Fuentes de verdad autorizadas (leer antes de escribir cualquier test)

En orden de precedencia:

1. `README.md` — comportamiento público, configuración, CLI y flujo operativo
2. `limitsdb/resources/ilm.example.yml` — contrato de políticas ILM y sus
   invariantes
3. `docs/configuration-contract.md` — contrato de configuración
4. `docs/exception-handling.md` — contratos de error documentados
5. Docstrings públicas de módulos y clases (no de funciones privadas)

**No leer el código fuente de la función bajo prueba antes de redactar el
test.** Leer primero la especificación. Escribir el test. Recién entonces
verificar que el test es ejecutable.

---

## Reglas obligatorias

### R1 — Nombrar el comportamiento, no la función

El nombre del test debe describir un comportamiento observable en términos del
dominio ILM, no el nombre de la función ni su mecanismo interno.

```python
# PROHIBIDO — nombra la función interna
def test_build_dependency_graph_and_layers(): ...
def test_plan_mode_handles_empty(): ...
def test_normalize_and_duplicate_detection(): ...

# CORRECTO — describe un comportamiento del dominio
def test_child_table_is_processed_after_its_parent(): ...
def test_plan_mode_reports_execution_order_without_connecting(): ...
def test_duplicate_table_in_ilm_file_is_rejected(): ...
```

### R2 — No llamar funciones privadas directamente

Una función con prefijo `_` es un detalle de implementación. Cambiarla no
debería romper ningún test si el comportamiento observable se preserva.

```python
# PROHIBIDO — el test queda acoplado al diseño interno
from limitsdb.core import ldb_runner
ldb_runner._build_dependency_graph(rows)
ldb_runner._compute_plan_layers(graph)
ldb_runner._plan_mode(config)

# CORRECTO — invocar a través de la interfaz pública del módulo o del CLI
from limitsdb.core.ldb_runner import run
result = run(config)
```

Excepción válida: una función privada que es el único punto de entrada a un
contrato documentado (p. ej., una función de carga con comportamiento de error
especificado en `docs/`). En ese caso documentar la excepción con un comentario
que cite la fuente.

### R3 — Citar la fuente de la especificación en cada test

Cada test debe incluir un comentario que identifique qué parte de la
especificación está verificando.

```python
def test_child_table_is_processed_after_its_parent():
    # Spec: README.md > "Execution order" — child tables depend on parent rows
    # existing before their own ILM runs; a parent-child cycle is an error.
    ...
```

Si no existe una fuente citable para el comportamiento que el test verifica,
ese test no debería existir o la especificación debe actualizarse primero.

### R4 — Usar estructura Given / When / Then explícita en el cuerpo

```python
def test_table_with_no_eligible_rows_produces_skipped_result():
    # Spec: README.md > ldb-run exit codes — SKIPPED cuando no hay filas
    # elegibles según la política de retención.

    # Given: política con retención de 3 meses; todas las filas son recientes
    config = ...
    engine = FakeEngine(rows=[])

    # When: se ejecuta el runner
    result = run(config, engine)

    # Then: el resultado es SKIPPED, no OK ni ERROR
    assert result == RunResult.SKIPPED
```

### R5 — Prohibir aserciones sobre estructura interna de diccionarios o tipos

```python
# PROHIBIDO — verifica la estructura interna del modelo
normalized_keys = set(ldb_ilm_config.IlmRule.__annotations__) - {"cond_expr"}
assert normalized_keys <= rows[0].keys()

# CORRECTO — verifica comportamiento observable: la regla cargada se puede
# ejecutar y produce los efectos documentados
rule = load_rule(path)
assert rule.retain_months_source == 3
assert rule.is_active
```

### R6 — Generar al menos un caso adversarial por comportamiento

Por cada comportamiento positivo, incluir al menos un caso que verifique qué
ocurre cuando la entrada viola la precondición documentada.

Fuentes de casos adversariales para este proyecto:

- Política ILM con `retain_months_source: 0`
- Tabla hijo con `referencing_tables` apuntando a una tabla inexistente
- Ciclo de dependencias entre tablas (padre → hijo → padre)
- Archivo ILM vacío o con `tables: []`
- Política con `is_active: false` en todas sus condiciones
- Secretos no cifrados cuando `enforce_encrypted_secrets` es `true`
- Fallo del worker en una tabla mientras otra completa exitosamente

### R7 — No mockear lo que se puede construir con datos controlados

Mockear la función que estás probando no añade información. Preferir fixtures
que representen estados reales del dominio.

```python
# PROHIBIDO — mockear la función que se quiere probar
monkeypatch.setattr(ldb_runner, "_load_offline_rows", lambda current: rows)
result = ldb_runner._plan_mode(config)  # sólo prueba que la función llama a la otra

# CORRECTO — construir un archivo ILM real en tmp_path y usar la interfaz pública
ilm_path = tmp_path / "ilm.yml"
ilm_path.write_text(yaml.safe_dump({"tables": [...]}), encoding="utf-8")
config = Config(schema="s", mode="PLAN", ilm_config_file=str(ilm_path))
result = run(config)
assert result == 0
```

Excepción válida: mockear la capa de base de datos (conexiones Oracle) está
justificado en tests locales porque requiere infraestructura externa. Los
contratos de `DatabaseEngine` son la especificación del mock.

---

## Lista de verificación antes de entregar un test

Responder sí a cada pregunta antes de considerar el test completo:

- [ ] ¿El nombre del test describe un comportamiento del dominio ILM?
- [ ] ¿Existe un comentario que cita la fuente de especificación?
- [ ] ¿El test invoca sólo interfaces públicas (salvo excepción documentada)?
- [ ] ¿Las aserciones verifican efectos observables, no estructura interna?
- [ ] ¿Hay al menos un caso adversarial para este comportamiento?
- [ ] ¿Si se introduce el bug que este test pretende detectar, el test falla?

La última pregunta es la más importante. Formularla explícitamente: *¿qué
cambio en el código haría fallar este test?* Si la respuesta es "no lo sé" o
"ninguno razonable", el test no está midiendo lo que debe.

---

## Cómo usar este documento

Incluir el contenido de este archivo al inicio de cualquier sesión o prompt
destinado a generar o revisar tests en LimitsDb, antes de proporcionar el
código fuente de los módulos bajo prueba.

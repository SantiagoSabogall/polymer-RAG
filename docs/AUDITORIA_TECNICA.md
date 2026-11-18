# Auditoría de software y seguridad del sistema WVTR

**Decisión de despliegue.** Corregir el bloqueo de la extracción y verificar la integridad de la sincronización y de las mediciones antes de publicar una nueva base consultable. Una segunda ejecución con los mismos PDFs puede eliminar los vectores de búsqueda. Una consulta puede seguir devolviendo resultados aunque recupere otra medición o atribuya mal la evidencia.

**Alcance y evidencia.** Revisión del árbol local sobre `2dbe14e`, incluidos los cambios pendientes y los módulos nuevos de visión. Se revisaron las dos CLI de ingesta, acceso a R2, conversión y limpieza, extracción, estado, SQL, embeddings, RAG, API, interfaz, benchmark, pruebas y configuración de instalación y despliegue. Las referencias de línea corresponden a este árbol. El comando `OPENROUTE_API='' .venv/bin/python -m unittest discover -s tests` descubrió 57 pruebas, aprobó 54 y omitió las 3 que requieren credenciales. Ruff pasó las reglas E9, F63, F7 y F82. Se hicieron comprobaciones adicionales con datos sintéticos, un cliente simulado y archivos temporales. Quedaron fuera de la verificación dinámica R2, OpenRouter, PostgreSQL, la exposición efectiva de red y el rendimiento bajo carga. Los fragmentos de corrección son propuestas.

## 1. Errores críticos y bugs

### Prioridad 0 · La CLI documentada se cuelga al guardar la primera extracción nueva

`main.py:361–363` adquiere `file_lock` y llama a `save_llm_results`, que intenta adquirir el mismo `threading.Lock` en la línea 117. El hilo que podría liberarlo queda esperando. Confirmé la segunda adquisición ejecutando la fase 2 con una respuesta LLM simulada y un lock instrumentado. El fallo aparece después de la llamada facturable y antes de guardar el resultado. La prueba de visión no atraviesa esta CLI.

La fase 2 de este archivo procesa los resultados en serie. El arreglo mínimo en el bucle es eliminar el bloqueo exterior y conservar el de la escritura.

```python
llm_result = extract_wvtr_with_retry(llm_client, markdown_text, doi, nombre)
add_llm_result(llm_results, pdf_key, doi, etag, llm_result)
save_llm_results(llm_results)
```

**Prueba de aceptación.** Ejecutar dos documentos con cliente simulado y un límite de tiempo. Ambos deben quedar guardados y una segunda ejecución debe hacer cero llamadas al modelo.

### Prioridad 1 · La fase 3 puede borrar vectores válidos y conservar mediciones caducadas

- **Reejecución sin cambios.** Ambas CLI recorren los resultados guardados. Para cada documento con registros, `replace_document` borra las filas y crea otras con nuevos identificadores y `embedding` nulo. La búsqueda vectorial exige `embedding IS NOT NULL`. Si se repite la sincronización después de indexar, esos artículos desaparecen de la rama semántica hasta regenerar vectores. Persistir por documento la versión *aplicada a PostgreSQL*, comprobarla dentro de la transacción y omitir las escrituras idénticas. [CLI de texto](../main.py#L374) · [CLI de visión](../main_test.py#L845) · [Reemplazo](../src/database.py#L223) · [Búsqueda](../src/rag.py#L76)
- **Resultado válido vacío.** Un artículo que pasa de tres mediciones a cero mantiene las tres anteriores porque la función retorna antes del borrado. A la vez, el extractor de texto convierte un campo `registros` ausente en lista vacía. Validar primero el esquema y representar el fallo por separado. Para una extracción válida, reemplazar por clave estable dentro de una transacción, incluso cuando la lista está vacía. [Persistencia](../src/database.py#L229) · [Extractor](../src/llm.py#L93)
- **Identidad inestable.** El borrado elige DOI cuando existe y restringe a `doi IS NULL` en el resto de los casos. Si un PDF adquiere DOI, su fila antigua puede sobrevivir. Si dos PDFs comparten DOI, uno puede borrar al otro. La extracción busca DOI en el documento crudo completo y prioriza el patrón de enlace, por lo que incluso puede escoger el DOI de una referencia antes que un DOI propio en otro formato. Usar la clave R2 como identidad del archivo y guardar el DOI como metadato con evidencia. Migrar explícitamente las filas históricas que guardaron el DOI en `pdf_url`. [Borrado](../src/database.py#L265) · [Carga histórica](../src/database.py#L137) · [Detección de DOI](../src/clean_markdown.py#L8)
- **Poda con inventarios incompatibles.** Se pasan claves R2 actuales junto a todos los DOI del JSON local, incluidos los antiguos. Un PDF retirado puede seguir en la base. Un JSON local parcialmente poblado y no vacío también puede causar el borrado de filas con DOI de otros PDFs que siguen en R2. Además, se puede marcar `pruned` tras `--no-prune` y la CLI de visión también lo hace cuando la BD devuelve error. Una clave podada que reaparece con el mismo ETag queda fuera del plan de conversión. Limitar la poda a documentos administrados cuya eliminación confirme un inventario completo. Registrar su resultado solo tras el commit y volver a encolar claves que reaparezcan. [Poda SQL](../src/database.py#L279) · [Orquestación](../main_test.py#L1004) · [Plan incremental](../src/sync_state.py#L48)

**Prueba de aceptación.** Sembrar una BD desechable con dos PDFs que comparten DOI y uno sin DOI. Indexar y repetir la sincronización con los mismos ETag. Deben conservarse los identificadores y vectores. Luego cambiar solo un PDF, publicar un resultado válido vacío, simular un fallo de extracción y retirar y restaurar una clave. Comprobar filas, estado y vectores tras cada operación.

### Prioridad 1 · El índice semántico representa el artículo en lugar de la medición

`generate_embeddings.py:28–39` selecciona `id, raw_json`. Ese JSON contiene el artículo completo, repetido en cada fila, mientras que `generate_record_text` busca polímero, valor, unidades y condiciones en el primer nivel. Con un artículo sintético de dos mediciones la entrada real del modelo fue únicamente `Source: Example`. Si falta el título también puede quedar vacía. Seleccionar las columnas de la medición y admitir el valor numérico cero en el formateador. [Generación](../scripts/generate_embeddings.py#L28) · [Formateador](../src/embeddings.py#L45) · [Origen del JSON](../src/database.py#L247)

```python
from psycopg2.extras import RealDictCursor

with conn.cursor(cursor_factory=RealDictCursor) as cur:
    cur.execute("""
        SELECT id, article_title, polymer, wvtr_value, wvtr_units,
               temperature, rh, thickness, test_method
        FROM wvtr_data WHERE embedding IS NULL
        ORDER BY id LIMIT %s
    """, (batch_size,))
    batch = cur.fetchall()

for row in batch:
    text = generate_record_text(row)
    # Solicitar el vector y guardar el resultado para row["id"].
```

Versionar el texto usado para indexar y regenerar también los vectores existentes afectados. El script actual hace un único commit al final y captura errores SQL sin rollback. Un error de escritura deja la transacción abortada y puede perderse el lote completo aunque el resumen anuncie todos como generados. Separar las llamadas remotas de las transacciones, aislar fallos por lote y contar filas confirmadas. [Escritura y resumen](../scripts/generate_embeddings.py#L42)

### Prioridad 1 · La reconciliación altera la evidencia científica

Con dos filas PLA de igual WVTR a 23 °C y 38 °C, `reconcile` devolvió una sola. La identidad usa polímero, valor redondeado y unidades, y omite las condiciones. Además, dos copias de la misma fila de imagen produjeron `text+image` con cero registros de texto. El texto numérico `"1.234"` produjo la clave 1234 y el número JSON `1.234` produjo 1.234. La primera conducta incluso tiene una prueba que la exige, pese a que el prompt pide preservar las mediciones por condición. [Identidad y normalización](../src/reconcile.py#L84) · [Fusión](../src/reconcile.py#L156) · [Prueba existente](../tests/test_vision_pipeline.py#L388) · [Contrato de extracción](../src/prompt.py)

Conservar la representación original, el tipo de magnitud y los calificadores. Fusionar únicamente observaciones compatibles con correspondencia inequívoca. Si faltan condiciones y hay varios candidatos, conservar ambas evidencias para revisión. Calcular la corroboración a partir de las modalidades realmente presentes. Fijar página y figura desde el manifiesto, pues `setdefault` acepta valores de procedencia ya suministrados por el modelo. Incluir la procedencia en recuperación y respuesta para que un investigador pueda verificar la tabla original. [Origen de imagen](../src/llm_vision.py#L158) · [Campos devueltos](../src/rag.py#L168)

### Prioridad 1 · La caché puede saltarse la actualización a visión o publicar una versión antigua

Confirmé que un JSON con el mismo ETag y versión `legacy` genera cero trabajos. El prefiltro revisa solo ETag y tamaño, y evita llegar a la comprobación de versión de `process_file`. Un `parse_error` guardado también puede quedar permanentemente omitido. El salto de fase 1 por ETag puede dejar los antiguos Markdown sin manifiesto. Además, la carga previa de todos los JSON de `temp/results` comparte directorio con experimentos de modelos y el merge conserva el resultado antiguo si la clave ya aparece en `seen`. Ejecutar directamente la fase 3 puede entonces publicar un artefacto obsoleto o experimental. [Prefiltro](../main_test.py#L743) · [Control interior](../main_test.py#L565) · [Carga y merge](../main_test.py#L977) · [Directorio de experimentos](../benchmark/run_model.py#L35)

Mantener una única comprobación de vigencia para cola, lectura y publicación. Verificar clave R2, ETag conocido, versión del conversor y extractor, modelo, esquema y estado satisfactorio. Distinguir «sin imágenes» de «manifiesto ausente». Guardar por separado los resultados parciales para reintentar solo la etapa fallida. En fase 3 construir la lista a partir del inventario actual de documentos y elegir el último artefacto válido de cada versión.

### Prioridad 1 · Colisiones de archivos y ejecución inesperada de llamadas facturables

- Dos claves como `lote-a/paper.pdf` y `lote-b/paper.pdf` comparten PDF temporal, Markdown, imágenes y archivo de resultados. Los workers pueden sobrescribir o borrar el PDF que otro está convirtiendo. El mapa `key_by_stem` también descarta una identidad en ejecución secuencial. Usar un directorio derivado de la clave completa en todas las etapas y migrar los artefactos históricos solo cuando la correspondencia sea única. Por ejemplo, `doc_dir = root / hashlib.sha256(pdf_key.encode("utf-8")).hexdigest()`. [Descarga](../main_test.py#L232) · [Mapeo](../main_test.py#L730) · [Ruta de texto](../main.py#L175)
- `main_test.py --phase 1` también entra en la fase 2 y puede enviar contenido y consumir saldo. Cambiar la segunda condición a `if args.phase in ("2", "all") and not args.dry_run:`. Probar que la fase 1 realiza cero peticiones al proveedor LLM. [Despacho](../main_test.py#L986)
- El semáforo de texto se libera antes de procesar imágenes. Cada documento crea su propio semáforo para clasificación y extracción. Con varios documentos las peticiones simultáneas pueden superar ampliamente los límites anunciados. Compartir un semáforo por operación y proveedor durante toda la ejecución y limitar también los documentos en vuelo. [Fase de texto](../main_test.py#L599) · [Inicio de visión](../main_test.py#L672) · [Extracción](../src/llm_vision.py#L178)

### Prioridad 1 si hay acceso compartido · Seguridad de API, administración y exportación

| Superficie | Exposición que muestra el código | Corrección |
|---|---|---|
| API y gasto | `/query` y `/search` aceptan peticiones sin autenticación de aplicación, cuotas ni límite útil para pregunta o resultados. Ambos pueden originar gasto. El servidor incluido escucha en loopback. El alcance real depende del proxy y de cómo se despliegue. [API](../api/server.py#L36) | Autenticar en el punto de entrada compartido, aplicar cuotas por identidad y limitar tamaño, concurrencia y tiempo. Restringir los orígenes CORS a los clientes previstos. |
| Administración | Compose publica PostgreSQL y Adminer en todas las interfaces por defecto. La configuración documentada además reutiliza el usuario de administración para Python. [Compose](../docker-compose.yml#L5) · [README](../README.md#L140) | Para uso local, publicar en `127.0.0.1:5433:5432` y `127.0.0.1:8080:8080`. Activar Adminer mediante perfil. Usar roles diferenciados para consultas, ingesta y migraciones. |
| Errores y datos de salida | `/health` devuelve la excepción al cliente con HTTP 200 aun al fallar la BD. La interfaz muestra excepciones crudas. `/query` devuelve también todo el contexto utilizado. [Salud](../api/server.py#L117) · [Interfaz](../app/chat.py#L167) | Devolver 503 con detalle genérico al fallar readiness, registrar internamente el diagnóstico con identificador y revisar qué contexto necesita el cliente. |
| Exportación Excel | El libro recibe campos de texto procedentes del modelo directamente como valores de celdas. Un texto que Excel interprete como fórmula dejaría de ser dato literal. [Exportador](../benchmark/generate_excel.py#L68) | Forzar tipo cadena en columnas textuales al escribir XLSX, mantener las numéricas tipadas y verificar los tipos al reabrir el libro. |
| PDFs y proveedores | Se obtiene el tamaño en R2 pero el trabajo de conversión y el envío del Markdown completo carecen de un presupuesto explícito por documento. La ruta de visión también transmite imágenes al proveedor. [Inventario](../src/cloudfareR2.py#L48) · [Conversión](../src/pdf_to_markdown.py#L61) · [Entrada al modelo](../main_test.py#L587) | Aplicar límites de bytes, páginas, píxeles, tokens y tiempo de conversión. Aislar el conversor y definir qué colecciones pueden salir al proveedor. |

Ejemplo de validación en el contrato actual. Los topes son valores iniciales propuestos y deben ajustarse al presupuesto por petición.

```python
from pydantic import BaseModel, Field, StringConstraints
from typing import Annotated

class QueryRequest(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True,
                                               min_length=1, max_length=2000)]
    limit: int = Field(default=10, ge=1, le=30)
```

### Prioridad 2 · Recuperación, benchmark y distribución

| Hallazgo | Efecto y corrección |
|---|---|
| Temperatura y ordenación | La consulta usa coincidencia parcial, trae todas las filas coincidentes sin orden y las antepone a las semánticas. Buscar 8 °C puede incluir 38 °C y desplazar al polímero consultado. Sin temperatura explícita hay una consulta adicional por cada temperatura faltante. Normalizar temperatura, rangos y unidades, aplicar filtros explícitos y ordenar candidatos por relevancia antes de un `LIMIT` en SQL. [Recuperación](../src/rag.py#L89) |
| Operador vectorial e índice | La consulta usa distancia L2 `<->` y el IVFFlat está definido para coseno. Ese índice no satisface esa ordenación. Si la métrica elegida es coseno, usar `ORDER BY embedding <=> %s::vector LIMIT %s` en ambas consultas. Comparar recuperación y `EXPLAIN (ANALYZE, BUFFERS)` con datos representativos. [SQL de consulta](../src/rag.py#L83) · [Índice](../docker/init.sql#L41) |
| Título opcional | La tabla permite títulos nulos y el RAG intenta recortarlos con `[:100]` y `[:60]`. Un registro así seleccionado causa una excepción y rompe la consulta completa. Usar el mismo formateador para contexto y fuentes, con DOI o «Título no disponible» como respaldo. [DDL](../docker/init.sql#L11) · [Contexto](../src/rag.py#L253) · [Fuentes](../src/rag.py#L369) |
| Validez del benchmark | Ninguno de los siete Markdown coincide con una entrada anotada del JSON de referencia. El evaluador confunde ausencia de anotación con un artículo sin mediciones. En una prueba sintética, PLA con valor 1 frente a PBAT con valor 100 dio precisión y recall de 1, porque bastó compartir campos ausentes. Excluir y contar casos sin anotar, exigir coincidencia en polímero, magnitud, unidades y valor con tolerancia acordada y evaluar las condiciones por separado. [Referencia](../benchmark/ground_truth.json) · [Asignación](../benchmark/evaluate.py#L86) |
| CLI del benchmark | La segunda definición de `main` oculta la corutina de igual nombre y se llama recursivamente al evaluar `asyncio.run(main(model))`. Renombrar la primera a `run_benchmark_async` y llamarla una vez desde la CLI. [Definiciones](../benchmark/run_model.py#L163) · [Entrada](../benchmark/run_model.py#L211) |
| Instalación | La entrada `pipeline = "main:main"` apunta a un módulo raíz ausente de la lista explícita de paquetes del wheel. El proyecto local además declara Pillow y pypdf como dependencias directas y el registro del proyecto en el lock aún no las incluye. Mover la CLI a un paquete, regenerar el lock y probar el wheel instalado desde fuera del checkout. El wheel no se construyó en esta revisión. [Empaquetado](../pyproject.toml#L56) · [Lock](../uv.lock#L2268) |
| Migración y ejecución | Al reaplicar `init.sql` sobre una tabla anterior se intenta indexar `source_kind` antes de añadirla. Los scripts de inicialización de imagen tampoco sustituyen una migración de volúmenes existentes. Introducir migraciones versionadas y probar una actualización desde el esquema anterior. [DDL](../docker/init.sql#L40) |
| Estado y salida del proceso | Las escrituras JSON truncan el archivo antes de completarlo. Un corte puede impedir que arranque la siguiente ejecución. El lock local tampoco protege dos procesos. Algunas rutas capturan fallos de R2 o BD y terminan normalmente. Escribir a un temporal contiguo y usar `os.replace`, coordinar procesos y devolver código de salida distinto de cero ante fases fallidas. [Archivos](../main_test.py#L153) · [Errores de fase](../main_test.py#L896) |

## 2. Mejoras inmediatas

En orden de retorno esperado después de corregir los bloqueos de integridad.

1. **Liberar conexiones durante la espera de red.** Calcular el embedding de la pregunta antes de pedir la conexión a PostgreSQL. El pool tiene un máximo predeterminado de diez y hoy cada petición retiene una conexión durante la llamada remota. Inicializar el pool una vez al arrancar el servicio. [Consulta](../src/rag.py#L67) · [Pool](../src/database.py#L23)
2. **Reducir trabajo por consulta.** Obtener los cuatro contadores con una sola sentencia agregada y guardar temporalmente el resultado. Para readiness basta una consulta barata. Medir latencia p95 y consultas SQL por petición antes y después. [Estadísticas](../src/rag.py#L185) · [Health](../api/server.py#L121)
3. **Verificar disponibilidad con costes controlados.** Hacer explícita la ejecución de los E2E de pago mediante una variable opt-in. Mantener en CI un recorrido con R2 y LLM simulados y PostgreSQL desechable. Cubrir `--phase`, `--no-prune`, reintento y doble ejecución. [E2E](../tests/test_vision_e2e.py#L52)
4. **Hacer fiel el diagnóstico.** Abrir el log durante el arranque de la CLI. Actualmente importar `main_test.py` construye un `FileHandler(mode="w")` y puede truncar el historial. Contar imágenes efectivamente procesadas después del tope y registrar por separado vacíos válidos, fallos de parseo y resultados parciales. [Logger](../main_test.py#L78) · [Contador](../main_test.py#L522)
5. **Alinear comandos y documentación.** El README ejecuta `main.py` y en otra sección llama entrada principal a `main_test.py`. Publicar una tabla de comandos y efectos hasta unificar ambas rutas. Centralizar identificadores de modelos y configuración de reintentos. [Ejecución](../README.md#L176) · [Estructura](../README.md#L370)
6. **Proteger el resultado tabular.** Validar localmente las respuestas de texto antes de almacenarlas y mantener por separado dato original, medición validada y motivo de rechazo. Añadir pruebas para cero, título nulo, campos ausentes, desigualdades y condiciones incompatibles. [Parseo](../src/llm.py#L92) · [Normalización](../src/reconcile.py#L59)

## 3. Lo que está bien hecho

| Puesto | Práctica que conviene preservar | Valor concreto |
|---|---|---|
| 1 | Separación de manifiesto, filtro, clasificación, extracción y reconciliación | Permite ensayar una decisión con una tabla conocida y localizar en qué etapa se pierde. Hay fixtures, pruebas de geometría y pruebas de selección. [Módulos y pruebas](../tests/test_vision_pipeline.py) |
| 2 | Transacción por documento y parámetros SQL | El borrado y la inserción por lotes comparten commit y rollback. La parametrización protege los valores SQL y `execute_values` reduce viajes a la BD. Esa base facilita corregir identidad y concurrencia. [Implementación](../src/database.py#L264) |
| 3 | Inventario R2 paginado y plan incremental en una función independiente | Se pueden probar cambios de ETag y migraciones de estado sin descargar un PDF. [Inventario](../src/cloudfareR2.py#L37) · [Plan](../src/sync_state.py#L48) |
| 4 | Filtro barato antes de la extracción visual y salida estructurada | El clasificador distingue tablas legibles, usa un esquema y cachea veredictos. El extractor limita las imágenes por documento. Son controles de coste concretos que pueden mantenerse al introducir un presupuesto global. [Clasificador](../src/vision_gate.py#L203) · [Extractor](../src/llm_vision.py#L170) |
| 5 | Captura de procedencia y condiciones experimentales | El esquema conserva unidades, humedad, temperatura, método y fuente. El prompt exige evidencia y admite condiciones desconocidas. Permite comprobar una respuesta contra el artículo cuando se conserva la trazabilidad hasta la API. [Contrato](../src/prompt.py) · [Tabla](../docker/init.sql#L9) |

## 4. Plan de refactorización

| Orden | Cambio y dependencia | Criterio de salida |
|---|---|---|
| 1 · Estabilizar ambas entradas | Aplicar las correcciones de lock, selección de fase, persistencia, caché y reconciliación. Crear pruebas pequeñas que ejerzan las CLI desde sus puntos de entrada. | Un fallo inyectado se comunica al programador de tareas. Reejecutar con el mismo inventario produce cero llamadas LLM, cero escrituras de mediciones y conserva los vectores. |
| 2 · Unificar el pipeline | Crear un paquete instalable con una CLI. Extraer funciones de conversión, extracción y publicación. Inyectar clientes R2, LLM y repositorio. Mantener texto y visión como opciones explícitas. | Los comandos documentados y el wheel recorren el mismo código. Los tests pueden sustituir cada servicio y dos documentos homónimos permanecen independientes. |
| 3 · Migrar estado e identidad a PostgreSQL | Crear documentos con clave R2 única, revisiones con ETag y versión del extractor, mediciones y evidencias asociadas. Guardar el JSON una vez por revisión. Serializar la publicación por documento, conservar la revisión previa ante fallo y marcar la nueva como vigente al confirmar. | Migración ensayada sobre copia con recuentos y muestras por documento. Dos workers concurrentes publican una única revisión completa. Eliminar y restaurar un PDF preserva la historia. |
| 4 · Escalar extracción e indexación | Introducir trabajos reanudables por revisión con límites globales y presupuesto de tokens. Encolar embeddings por contenido validado y versión de representación. Exponer métricas de pendientes, coste, latencia y errores por etapa. | Un reinicio a mitad de lote recupera solo lo pendiente. Un cambio en una medición reindexa únicamente las afectadas. |
| 5 · Validar búsqueda y calidad científica | Formar un conjunto anotado de artículos y preguntas con disciplinas de unidades, condiciones y procedencia. Evaluar extracción por medición y recuperación por consulta. Ajustar los índices sobre la distribución real. | Publicar cobertura anotada, precisión y recall de extracción, acierto de recuperación en los primeros k resultados, coste por artículo y latencia p95 con una carga reproducible. |

**Orden de inversión.** Resolver primero integridad y repetibilidad. Cambiar de modelo, ampliar el pool o añadir workers antes de fijar estas propiedades haría más costoso distinguir una mejora real de un resultado sobrescrito o mal medido.

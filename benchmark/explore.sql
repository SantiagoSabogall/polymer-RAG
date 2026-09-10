-- ============================================
-- EXPLORACIÓN DE BASE DE DATOS: polymers_wvtr
-- ============================================
-- Ejecutar desde psql o DBeaver
-- Conexión: localhost/polymers_wvtr (postgres/Colombia2030)

-- 1. Ver estructura de la tabla
\d wvtr_data

-- 2. Conteo total
SELECT COUNT(*) as total_registros FROM wvtr_data;

-- 3. Ver todos los datos (ordenados por artículo)
SELECT 
    id,
    article_title,
    doi,
    polymer,
    wvtr_value,
    wvtr_units,
    temperature,
    rh,
    thickness,
    test_method
FROM wvtr_data 
ORDER BY article_title, polymer;

-- 4. Estadísticas por polímero
SELECT 
    polymer,
    COUNT(*) as veces_aparece,
    ROUND(AVG(wvtr_value)::numeric, 2) as promedio_wvtr,
    ROUND(MIN(wvtr_value)::numeric, 2) as min_wvtr,
    ROUND(MAX(wvtr_value)::numeric, 2) as max_wvtr,
    STRING_AGG(DISTINCT wvtr_units, ', ') as unidades
FROM wvtr_data 
GROUP BY polymer 
ORDER BY veces_aparece DESC;

-- 5. Estadísticas por artículo
SELECT 
    article_title,
    COUNT(*) as registros,
    STRING_AGG(DISTINCT polymer, ', ') as polimeros
FROM wvtr_data 
GROUP BY article_title 
ORDER BY registros DESC;

-- 6. Polímeros únicos
SELECT DISTINCT polymer FROM wvtr_data ORDER BY polymer;

-- 7. Artículos con más registros
SELECT 
    article_title,
    COUNT(*) as total_registros
FROM wvtr_data 
GROUP BY article_title 
ORDER BY total_registros DESC 
LIMIT 10;

-- 8. Buscar polímero específico (ejemplo: PBAT)
SELECT * FROM wvtr_data 
WHERE polymer ILIKE '%PBAT%'
ORDER BY wvtr_value;

-- 9. Buscar por artículo específico
SELECT * FROM wvtr_data 
WHERE article_title ILIKE '%cellulose%'
ORDER BY polymer;

-- 10. Ver raw_json de un registro específico
SELECT 
    article_title,
    polymer,
    wvtr_value,
    raw_json
FROM wvtr_data 
WHERE id = 1;

-- 11. Exportar a CSV (desde psql)
\copy (SELECT * FROM wvtr_data ORDER BY id) TO 'temp/wvtr_data.csv' WITH CSV HEADER;

-- 12. Resumen general
SELECT 
    COUNT(*) as total_registros,
    COUNT(DISTINCT article_title) as articulos,
    COUNT(DISTINCT doi) as dois,
    COUNT(DISTINCT polymer) as polimeros,
    ROUND(AVG(wvtr_value)::numeric, 2) as promedio_general,
    ROUND(MIN(wvtr_value)::numeric, 2) as minimo,
    ROUND(MAX(wvtr_value)::numeric, 2) as maximo
FROM wvtr_data;

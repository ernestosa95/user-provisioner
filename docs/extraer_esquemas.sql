-- Ejecutar en SSMS conectado a la instancia de un sitio de TEST.
-- Pegar el resultado completo (Results to Text o copiar la grilla) en el chat.
-- No lee datos de pacientes ni de usuarios: solo la estructura de las tablas.

-- 1) ExtensaRadio
USE ExtensaRadio;
SELECT 'ExtensaRadio' AS db, t.TABLE_SCHEMA, t.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE,
       c.CHARACTER_MAXIMUM_LENGTH AS len, c.IS_NULLABLE, c.COLUMN_DEFAULT
FROM INFORMATION_SCHEMA.TABLES t
JOIN INFORMATION_SCHEMA.COLUMNS c ON c.TABLE_SCHEMA=t.TABLE_SCHEMA AND c.TABLE_NAME=t.TABLE_NAME
WHERE t.TABLE_SCHEMA='ExtRadio'
  AND t.TABLE_NAME IN ('tbUser','lsRole','lsPrefix','lsBusinessUnit','lsBusinessUnitUser','lsUserType','lsRoleUserType')
ORDER BY t.TABLE_NAME, c.ORDINAL_POSITION;

-- 2) SL_UserAndConfig
USE SL_UserAndConfig;
SELECT 'SL_UserAndConfig' AS db, t.TABLE_SCHEMA, t.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE,
       c.CHARACTER_MAXIMUM_LENGTH AS len, c.IS_NULLABLE, c.COLUMN_DEFAULT
FROM INFORMATION_SCHEMA.TABLES t
JOIN INFORMATION_SCHEMA.COLUMNS c ON c.TABLE_SCHEMA=t.TABLE_SCHEMA AND c.TABLE_NAME=t.TABLE_NAME
WHERE t.TABLE_SCHEMA='ExtConfig'
  AND t.TABLE_NAME IN ('User','UserProfile','UserProfile_User','Domain')
ORDER BY t.TABLE_NAME, c.ORDINAL_POSITION;

-- 3) Triggers sobre esas tablas (pueden afectar los INSERT)
SELECT 'ExtensaRadio' AS db, OBJECT_NAME(parent_id) AS tabla, name AS trigger_name
FROM ExtensaRadio.sys.triggers WHERE OBJECT_NAME(parent_id) IN ('tbUser','lsBusinessUnitUser')
UNION ALL
SELECT 'SL_UserAndConfig', OBJECT_NAME(parent_id), name
FROM SL_UserAndConfig.sys.triggers WHERE OBJECT_NAME(parent_id) IN ('User','UserProfile_User');

-- 4) Perfiles y dominios existentes (nombres solamente)
SELECT 'UserProfile' AS que, name FROM SL_UserAndConfig.ExtConfig.UserProfile
UNION ALL SELECT 'Domain', name FROM SL_UserAndConfig.ExtConfig.Domain
UNION ALL SELECT 'lsRole', Description FROM ExtensaRadio.ExtRadio.lsRole;

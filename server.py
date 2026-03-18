"""
AS/400 MCP Server — Servidor MCP para conectar asistentes de IA con IBM i (AS/400).

Proporciona herramientas seguras de solo lectura, compilacion y analisis
a traves del Model Context Protocol (MCP) via ODBC.
"""

import os
import re
import json
import logging
import pyodbc
import textwrap
from typing import Optional
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------
load_dotenv()

mcp = FastMCP("as400-mcp-server")

logger = logging.getLogger("as400-mcp")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],   # stderr — compatible con stdio MCP
)

# ---------------------------------------------------------------------------
# Constantes de seguridad
# ---------------------------------------------------------------------------

# Regex para validar identificadores de objetos AS/400.
# Nombres validos: 1-10 caracteres, mayusculas, alfanumerico + # @ $
AS400_ID_PATTERN = re.compile(r"^[A-Z0-9#@$]{1,10}$")

# Whitelist de comandos CL permitidos.
# Todo comando que NO coincida con esta lista sera rechazado.
#
# Criterio: solo se permiten comandos de visualizacion (DSP*), verificacion,
# compilacion, y manipulacion de sesion (library list, overrides).
#
# NOTA sobre RUNSQLSTM: esta BLOQUEADO porque permite ejecutar cualquier
# sentencia SQL (INSERT, DELETE, DROP, etc.), lo cual bypasea la validacion
# de solo lectura de execute_sql. Si necesitas habilitarlo, agregalo a
# ALLOWED_CL_COMMANDS con el riesgo que implica.
ALLOWED_CL_PREFIXES = (
    # Visualizacion / analisis (solo lectura)
    "DSP",       # DSPFD, DSPFFD, DSPPGMREF, DSPJOBLOG, DSPOBJD, DSPDBR, etc.
    "WRK",       # WRKSPLF, WRKOBJ — la mayoria necesita pantalla, pero algunos
                 # soportan OUTPUT(*OUTFILE) o OUTPUT(*PRINT)
)

ALLOWED_CL_COMMANDS = (
    # Verificacion
    "CHKOBJ",
    # Compilacion
    "CRTBNDRPG", "CRTRPGMOD", "CRTBNDCL",  "CRTSQLRPGI",
    "CRTPGM",    "CRTSRVPGM", "CRTBNDCBL", "CRTSQLCBLI",
    "CRTCMOD",   "CRTBNDC",   "CRTRPGPGM", "CRTCLPGM",
    # Sesion (afectan solo el job actual, no el sistema)
    "ADDLIBLE",  "RMVLIBLE",  "CHGCURLIB",
    # Overrides de sesion
    "OVRDBF",    "DLTOVR",
    # Informativo
    "SNDPGMMSG",
)

# Comandos de compilacion especificos (subconjunto para compile_program)
COMPILE_COMMANDS = (
    "CRTBNDRPG", "CRTRPGMOD", "CRTBNDCL",  "CRTSQLRPGI",
    "CRTPGM",    "CRTSRVPGM", "CRTBNDCBL", "CRTSQLCBLI",
    "CRTCMOD",   "CRTBNDC",   "CRTRPGPGM", "CRTCLPGM",
)


# ---------------------------------------------------------------------------
# Funciones de validacion
# ---------------------------------------------------------------------------

def _validate_identifier(value: str, label: str) -> str:
    """Valida y normaliza un identificador de objeto AS/400.

    Retorna el valor en mayusculas si es valido.
    Lanza ValueError si no cumple el patron.
    """
    normalized = value.strip().upper()
    if not AS400_ID_PATTERN.match(normalized):
        raise ValueError(
            f"{label} invalido: '{value}'. "
            f"Debe tener 1-10 caracteres alfanumericos (A-Z, 0-9, #, @, $)."
        )
    return normalized


def _validate_sql(sql: str) -> str:
    """Valida que una consulta SQL sea de solo lectura.

    Retorna el SQL limpio si pasa las validaciones.
    Lanza ValueError si se detecta una operacion no permitida.
    """
    # Remover comentarios SQL
    cleaned = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
    cleaned = cleaned.strip()

    if not cleaned:
        raise ValueError("La consulta SQL esta vacia.")

    # Bloquear punto y coma (previene statement stacking)
    if ";" in cleaned:
        raise ValueError(
            "No se permiten punto y coma (;) en las consultas. "
            "Envia una sola sentencia SELECT por vez."
        )

    upper = cleaned.upper()

    # Solo permitir SELECT, WITH, VALUES
    if not upper.startswith(("SELECT", "WITH", "VALUES")):
        raise ValueError(
            "Solo se permiten consultas de lectura (SELECT, WITH, VALUES)."
        )

    # Bloquear SELECT INTO (escribe datos)
    if re.search(r"\bSELECT\b.*\bINTO\b", upper):
        raise ValueError("SELECT INTO no esta permitido. Solo consultas de lectura.")

    # Bloquear CALL embebido
    if re.search(r"\bCALL\b", upper):
        raise ValueError("CALL no esta permitido dentro de consultas SQL.")

    # Safety net: si no tiene FETCH FIRST, agregar limite
    if "FETCH FIRST" not in upper and "LIMIT " not in upper:
        cleaned = cleaned.rstrip().rstrip(";")
        cleaned += " FETCH FIRST 1000 ROWS ONLY"
        logger.info("Se agrego FETCH FIRST 1000 ROWS ONLY como limite de seguridad.")

    return cleaned


def _validate_cl_command(comando: str) -> str:
    """Valida que un comando CL este en la whitelist.

    Retorna el comando original si esta permitido.
    Lanza ValueError si el comando no esta en la lista.
    """
    cmd_stripped = comando.strip()
    if not cmd_stripped:
        raise ValueError("El comando CL esta vacio.")

    # Extraer el verbo del comando (primer token antes de espacio o /)
    match = re.match(r"^([A-Za-z][A-Za-z0-9]*)", cmd_stripped)
    if not match:
        raise ValueError(f"Formato de comando invalido: '{comando}'")

    verb = match.group(1).upper()

    # Verificar contra comandos exactos permitidos
    if verb in ALLOWED_CL_COMMANDS:
        return cmd_stripped

    # Verificar contra prefijos permitidos
    for prefix in ALLOWED_CL_PREFIXES:
        if verb.startswith(prefix):
            return cmd_stripped

    raise ValueError(
        f"Comando '{verb}' no permitido. "
        f"Solo se permiten comandos de visualizacion (DSP*), verificacion, "
        f"compilacion (CRT*) y manejo de sesion. "
        f"Consulta la documentacion para ver la lista completa."
    )


def _sanitize_error(error: Exception) -> str:
    """Sanitiza un mensaje de error para no exponer informacion sensible."""
    msg = str(error)
    # Redactar credenciales y datos de conexion
    msg = re.sub(r"(UID|PWD|SYSTEM|DRIVER|Password|User)=[^;,\s]*", r"\1=***", msg, flags=re.IGNORECASE)
    # Truncar mensajes muy largos
    if len(msg) > 500:
        msg = msg[:500] + "..."
    return msg


# ---------------------------------------------------------------------------
# Conexion a base de datos
# ---------------------------------------------------------------------------

_connection: pyodbc.Connection | None = None


def _get_connection() -> pyodbc.Connection:
    """Obtiene una conexion reutilizable al AS/400.

    Reutiliza la conexion existente si esta activa.
    Crea una nueva si no existe o si la anterior esta muerta.
    """
    global _connection

    if _connection is not None:
        try:
            # Health check: verifica que la conexion siga viva
            _connection.execute("SELECT 1 FROM SYSIBM.SYSDUMMY1")
            return _connection
        except Exception:
            logger.warning("Conexion perdida, reconectando...")
            try:
                _connection.close()
            except Exception:
                pass
            _connection = None

    host = os.getenv("DB_HOST", "")
    user = os.getenv("DB_USER", "")
    password = os.getenv("DB_PASS", "")

    if not host:
        raise ValueError("DB_HOST no esta configurado. Revisa tu archivo .env")

    conn_str = (
        f"DRIVER={{IBM i Access ODBC Driver}};"
        f"SYSTEM={host};UID={user};PWD={password};"
    )
    _connection = pyodbc.connect(conn_str)
    logger.info("Conexion establecida con %s", host)
    return _connection


# ---------------------------------------------------------------------------
# Herramientas MCP
# ---------------------------------------------------------------------------

@mcp.tool()
def execute_sql(sql: str) -> str:
    """
    Ejecuta una consulta SQL SELECT en el servidor AS/400 (IBM i).
    Solo se permiten consultas de lectura (SELECT, WITH, VALUES).
    Ejemplo: SELECT * FROM QSYS2.SYSTABLES FETCH FIRST 5 ROWS ONLY
    """
    logger.info("execute_sql invocado | SQL: %.200s", sql)

    try:
        validated_sql = _validate_sql(sql)
    except ValueError as e:
        logger.warning("SQL rechazado: %s", e)
        return f"Error de validacion: {e}"

    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(validated_sql)

        columnas = [col[0] for col in cursor.description]
        resultados = cursor.fetchall()

        datos = [dict(zip(columnas, tuple(fila))) for fila in resultados]
        logger.info("execute_sql completado | %d filas retornadas", len(datos))
        return json.dumps(datos, indent=2, default=str)

    except Exception as e:
        logger.error("Error en execute_sql: %s", e)
        return f"Error al ejecutar SQL: {_sanitize_error(e)}"


@mcp.tool()
def execute_cl_command(comando: str) -> str:
    """
    Ejecuta un comando CL nativo del AS/400 (IBM i).
    Solo se permiten comandos de visualizacion, compilacion y manejo de sesion.
    IMPORTANTE: ODBC no muestra pantallas. Si el comando genera informacion,
    usa OUTPUT(*OUTFILE) para guardar el resultado en QTEMP,
    y luego usa 'execute_sql' para leer ese archivo.
    Ejemplo: DSPFD FILE(MI_LIB/MI_ARCHIVO) TYPE(*BASATR) OUTPUT(*OUTFILE) OUTFILE(QTEMP/FDA)
    """
    logger.info("execute_cl_command invocado | Comando: %.200s", comando)

    try:
        validated_cmd = _validate_cl_command(comando)
    except ValueError as e:
        logger.warning("Comando CL rechazado: %s", e)
        return f"Error de validacion: {e}"

    try:
        conn = _get_connection()
        cursor = conn.cursor()

        # Escapar comillas simples para el SQL wrapper
        comando_seguro = validated_cmd.replace("'", "''")
        sql = f"CALL QSYS2.QCMDEXC('{comando_seguro}')"

        cursor.execute(sql)
        conn.commit()

        logger.info("Comando CL ejecutado exitosamente: %.100s", validated_cmd)
        return f"Comando CL ejecutado exitosamente: {validated_cmd}"

    except Exception as e:
        logger.error("Error en execute_cl_command: %s", e)
        return f"Error al ejecutar el comando CL: {_sanitize_error(e)}"


@mcp.tool()
def read_source_member(biblioteca: str, archivo_fuente: str, miembro: str) -> str:
    """
    Lee el codigo fuente de un miembro especifico en el AS/400.
    Ideal para ver el codigo de un programa y analizar su logica o llamadas.
    Ejemplo: biblioteca='MYLIB', archivo_fuente='QRPGLESRC', miembro='MYPGM'
    """
    logger.info("read_source_member | %s/%s(%s)", biblioteca, archivo_fuente, miembro)

    try:
        lib = _validate_identifier(biblioteca, "Biblioteca")
        srcf = _validate_identifier(archivo_fuente, "Archivo fuente")
        mbr = _validate_identifier(miembro, "Miembro")
    except ValueError as e:
        logger.warning("Identificador rechazado: %s", e)
        return f"Error de validacion: {e}"

    # Alias unico para evitar colisiones
    alias = f"QTEMP.SRC{mbr[:7]}"

    try:
        conn = _get_connection()
        cursor = conn.cursor()

        # Limpiar alias previo si quedo colgado
        try:
            cursor.execute(f"DROP ALIAS {alias}")
        except Exception:
            pass

        # Crear alias apuntando al miembro fuente
        cursor.execute(f"CREATE ALIAS {alias} FOR {lib}.{srcf}({mbr})")

        # Leer el codigo fuente
        cursor.execute(f"SELECT SRCSEQ, SRCDTA FROM {alias} ORDER BY SRCSEQ")

        lineas = []
        for fila in cursor.fetchall():
            seq = f"{fila[0]:06.2f}" if fila[0] is not None else "000000"
            data = fila[1].rstrip() if fila[1] else ""
            lineas.append(f"{seq} | {data}")

        # Limpiar el alias
        try:
            cursor.execute(f"DROP ALIAS {alias}")
            conn.commit()
        except Exception:
            pass

        logger.info("Fuente leida: %s/%s(%s) — %d lineas", lib, srcf, mbr, len(lineas))
        return "\n".join(lineas)

    except Exception as e:
        # Intentar limpiar el alias en caso de error
        try:
            cursor.execute(f"DROP ALIAS {alias}")
        except Exception:
            pass
        logger.error("Error en read_source_member: %s", e)
        return f"Error al leer el codigo fuente: {_sanitize_error(e)}"


@mcp.tool()
def compile_program(
    library: str,
    source_file: str,
    member: str,
    command: str = "CRTBNDRPG",
    options: str = "",
) -> str:
    """
    Compila un programa en el AS/400 (IBM i).
    Comandos soportados: CRTBNDRPG, CRTRPGMOD, CRTBNDCL, CRTSQLRPGI,
    CRTPGM, CRTSRVPGM, CRTBNDCBL, CRTSQLCBLI, CRTCMOD, CRTBNDC, CRTRPGPGM, CRTCLPGM.
    Despues de compilar, usa read_spoolfile para ver los errores/resultados.
    Ejemplo: library='MYLIB', source_file='QRPGLESRC', member='MYPGM'
    """
    logger.info("compile_program | %s/%s(%s) con %s", library, source_file, member, command)

    try:
        lib = _validate_identifier(library, "Biblioteca")
        srcf = _validate_identifier(source_file, "Archivo fuente")
        mbr = _validate_identifier(member, "Miembro")
    except ValueError as e:
        logger.warning("Identificador rechazado: %s", e)
        return f"Error de validacion: {e}"

    cmd_upper = command.strip().upper()
    if cmd_upper not in COMPILE_COMMANDS:
        return (
            f"Error: Comando de compilacion '{command}' no soportado. "
            f"Comandos validos: {', '.join(COMPILE_COMMANDS)}"
        )

    # Sanitizar options: solo permitir caracteres seguros para parametros CL
    if options and not re.match(r"^[A-Za-z0-9\s\(\)\*/\.'_\-]+$", options):
        return "Error: El parametro 'options' contiene caracteres no permitidos."

    # Construir el comando CL de compilacion
    cl_command = (
        f"{cmd_upper} PGM({lib}/{mbr}) "
        f"SRCFILE({lib}/{srcf}) SRCMBR({mbr})"
    )
    if options.strip():
        cl_command += f" {options.strip()}"

    try:
        conn = _get_connection()
        cursor = conn.cursor()

        comando_seguro = cl_command.replace("'", "''")
        sql = f"CALL QSYS2.QCMDEXC('{comando_seguro}')"

        cursor.execute(sql)
        conn.commit()

        logger.info("Compilacion exitosa: %s", cl_command)
        return (
            f"Compilacion exitosa: {cl_command}\n"
            f"Usa read_spoolfile() para ver el detalle de la compilacion."
        )

    except Exception as e:
        logger.error("Error en compilacion: %s", e)
        error_msg = _sanitize_error(e)
        return (
            f"Error de compilacion: {error_msg}\n"
            f"Usa read_spoolfile() para ver los errores detallados."
        )


"""
read_spoolfile — reemplazo para el MCP as400-mcp-server
========================================================
Reemplaza la función read_spoolfile existente en el servidor MCP.
 
Cambios respecto a la versión anterior:
  - Busca spoolfiles por NOMBRE DE PROGRAMA en JOB_NAME de QEZJOBLOG,
    no solo por usuario conectado. Esto cubre el caso de uso principal:
    "dame el último spoolfile del programa FLALLQRY2P".
  - Lee el contenido via SYSTOOLS.SPOOLED_FILE_DATA — sin CPYSPLF,
    sin archivos en QTEMP, sin await.
  - Sigue los mismos patrones del MCP: def sincrónico, retorna str,
    _validate_identifier, _sanitize_error, _get_connection, logging.
 
Modos de uso:
  1. Sin parámetros         → lista los 10 spoolfiles más recientes del usuario DB_USER
  2. program_name solamente → busca en QEZJOBLOG y lee el más reciente del programa
  3. job_name + spoolfile_name (comportamiento original) → lee ese spoolfile exacto
""" 
 
@mcp.tool()
def read_spoolfile(
    user: str = "",
    spoolfile_name: str = "",
    job_name: str = "",
    spoolfile_number: int = 0,
    program_name: str = "",
    max_lines: int = 5000,
) -> str:
    """
    Lee spoolfiles (spool) del AS/400. Soporta tres modos:
 
    MODO 1 — Listar spoolfiles recientes del usuario conectado:
      read_spoolfile()
      read_spoolfile(user='NYLPGRFL')
 
    MODO 2 — Leer el último spoolfile de un programa (caso más común):
      read_spoolfile(program_name='FLALLQRY2P')
      read_spoolfile(program_name='FLALLQRY2P', spoolfile_name='QPJOBLOG')
      Busca en QEZJOBLOG/QUSRSYS el job más reciente cuyo nombre contiene
      el programa, y lee su contenido completo.
 
    MODO 3 — Leer un spoolfile específico por job_name (comportamiento original):
      read_spoolfile(job_name='652459/NYLPGRFL/FLALLQRY2P',
                     spoolfile_name='QPJOBLOG', spoolfile_number=1)
 
    Args:
        user:             Usuario AS/400 (default: DB_USER del .env).
        spoolfile_name:   Nombre del archivo spool (ej: 'QPJOBLOG', 'QSYSPRT').
                          En modo 2 se puede omitir; se usa el que se encuentre.
        job_name:         Job completo en formato 'NNNNNN/USUARIO/NOMBRE_JOB'.
                          Requerido en modo 3.
        spoolfile_number: Número de spool dentro del job (default: el que
                          encuentre la búsqueda, o 1 si se especifica job_name).
        program_name:     Nombre del programa a buscar en QEZJOBLOG (modo 2).
                          Ej: 'FLALLQRY2P'. Activa el modo 2.
        max_lines:        Máximo de líneas a retornar (default 5000).
    """
    logger.info(
        "read_spoolfile | user=%s, spool=%s, job=%s, num=%d, pgm=%s",
        user, spoolfile_name, job_name, spoolfile_number, program_name,
    )
 
    effective_user = user.strip().upper() or os.getenv("DB_USER", "").upper()
    if not effective_user:
        return "Error: No se pudo determinar el usuario. Configura DB_USER en .env."
 
    try:
        conn = _get_connection()
        cursor = conn.cursor()
 
        # ------------------------------------------------------------------
        # MODO 2: buscar por nombre de programa en QEZJOBLOG
        # ------------------------------------------------------------------
        if program_name and not job_name:
            pgm = program_name.strip().upper()
 
            # Validar nombre del programa como identificador AS/400
            try:
                pgm = _validate_identifier(pgm, "Nombre de programa")
            except ValueError as e:
                return f"Error de validacion: {e}"
 
            # Buscar el spoolfile más reciente del programa en QEZJOBLOG.
            # JOB_NAME tiene formato NNNNNN/USUARIO/NOMBRE_PGM — filtramos
            # por el sufijo /<pgm> para no mezclar con otros jobs.
            spool_filter = (
                f"AND UPPER(SPOOLED_FILE_NAME) = '{spoolfile_name.strip().upper()}' "
                if spoolfile_name.strip() else ""
            )
 
            sql_find = (
                "SELECT "
                "  CAST(JOB_NAME          AS VARCHAR(28)) AS JOB_NAME, "
                "  CAST(SPOOLED_FILE_NAME AS VARCHAR(10)) AS SPOOL_FILE, "
                "  CAST(FILE_NUMBER       AS VARCHAR(5))  AS FILE_NUM, "
                "  CAST(CREATE_TIMESTAMP  AS VARCHAR(26)) AS FECHA, "
                "  CAST(STATUS            AS VARCHAR(15)) AS ESTADO, "
                "  CAST(USER_NAME         AS VARCHAR(10)) AS USUARIO, "
                "  CAST(TOTAL_PAGES       AS VARCHAR(10)) AS PAGINAS, "
                "  CAST(SIZE              AS VARCHAR(15)) AS SIZE "
                "FROM QSYS2.OUTPUT_QUEUE_ENTRIES "
                "WHERE OUTPUT_QUEUE_NAME         = 'QEZJOBLOG' "
                "  AND OUTPUT_QUEUE_LIBRARY_NAME = 'QUSRSYS' "
                f"  AND UPPER(JOB_NAME) LIKE '%/{pgm}' "
                f"  {spool_filter}"
                "ORDER BY CREATE_TIMESTAMP DESC "
                "FETCH FIRST 1 ROWS ONLY"
            )
 
            cursor.execute(sql_find)
            columnas = [col[0] for col in cursor.description]
            fila = cursor.fetchone()
 
            if not fila:
                return (
                    f"No se encontró ningún spoolfile para el programa '{pgm}' "
                    f"en QEZJOBLOG/QUSRSYS."
                )
 
            info = dict(zip(columnas, tuple(fila)))
            resolved_job    = info["JOB_NAME"].strip()
            resolved_spool  = info["SPOOL_FILE"].strip()
            resolved_num    = int(info["FILE_NUM"].strip())
            resolved_fecha  = info["FECHA"].strip()
            resolved_user   = info["USUARIO"].strip()
            resolved_paginas = info["PAGINAS"].strip()
 
            header = (
                f"=== Spoolfile encontrado ===\n"
                f"  Programa   : {pgm}\n"
                f"  Job        : {resolved_job}\n"
                f"  Spool file : {resolved_spool}  (#{resolved_num})\n"
                f"  Creado     : {resolved_fecha}\n"
                f"  Usuario    : {resolved_user}\n"
                f"  Páginas    : {resolved_paginas}\n"
                f"{'=' * 60}\n\n"
            )
 
            return header + _read_spool_content(
                cursor, resolved_job, resolved_spool, resolved_num, max_lines
            )
 
        # ------------------------------------------------------------------
        # MODO 3: leer spoolfile específico por job_name
        # ------------------------------------------------------------------
        if job_name and spoolfile_name:
            try:
                spool = _validate_identifier(spoolfile_name.strip().upper(), "Nombre de spoolfile")
            except ValueError as e:
                return f"Error de validacion: {e}"
 
            if not re.match(r"^[A-Za-z0-9]+/[A-Za-z0-9#@$]+/[A-Za-z0-9_]+$", job_name.strip()):
                return "Error: job_name debe tener formato 'NUMERO/USUARIO/NOMBRE_JOB'."
 
            spool_num = max(spoolfile_number, 1)
            return _read_spool_content(cursor, job_name.strip(), spool, spool_num, max_lines)
 
        # ------------------------------------------------------------------
        # MODO 1: listar spoolfiles recientes del usuario
        # ------------------------------------------------------------------
        sql_list = (
            "SELECT "
            "  CAST(SPOOLED_FILE_NAME  AS VARCHAR(10)) AS SPOOLED_FILE_NAME, "
            "  CAST(JOB_NAME           AS VARCHAR(28)) AS JOB_NAME, "
            "  CAST(CREATE_TIMESTAMP   AS VARCHAR(26)) AS CREATED, "
            "  CAST(STATUS             AS VARCHAR(15)) AS STATUS, "
            "  CAST(TOTAL_PAGES        AS VARCHAR(10)) AS TOTAL_PAGES, "
            "  CAST(FILE_NUMBER        AS VARCHAR(5))  AS SPOOLED_FILE_NUMBER "
            "FROM QSYS2.OUTPUT_QUEUE_ENTRIES_BASIC "
            f"WHERE USER_NAME = '{effective_user}' "
            "ORDER BY CREATE_TIMESTAMP DESC "
            "FETCH FIRST 10 ROWS ONLY"
        )
 
        cursor.execute(sql_list)
        columnas = [col[0] for col in cursor.description]
        resultados = cursor.fetchall()
 
        if not resultados:
            return f"No se encontraron spoolfiles para el usuario {effective_user}."
 
        datos = [dict(zip(columnas, tuple(fila))) for fila in resultados]
        logger.info("Listado de spoolfiles: %d encontrados", len(datos))
        return json.dumps(datos, indent=2, default=str)
 
    except Exception as e:
        logger.error("Error en read_spoolfile: %s", e)
        return f"Error al leer spoolfile: {_sanitize_error(e)}"
 
 
def _read_spool_content(
    cursor: "pyodbc.Cursor",
    job_name: str,
    spooled_file_name: str,
    spooled_file_number: int,
    max_lines: int,
) -> str:
    """
    Helper interno: lee el contenido de un spoolfile via SYSTOOLS.SPOOLED_FILE_DATA.
    Reutilizado por modo 2 y modo 3 de read_spoolfile.
 
    No crea archivos en QTEMP ni usa CPYSPLF — lee directo desde la vista de sistema.
    Retorna el contenido como texto plano, truncado a max_lines si corresponde.
    """
    sql = (
        "SELECT SPOOLED_DATA "
        "FROM TABLE(SYSTOOLS.SPOOLED_FILE_DATA("
        f"JOB_NAME => '{job_name}', "
        f"SPOOLED_FILE_NAME => '{spooled_file_name}', "
        f"SPOOLED_FILE_NUMBER => {spooled_file_number}"
        "))"
    )
 
    cursor.execute(sql)
    lineas = []
 
    for fila in cursor.fetchall():
        lineas.append(str(fila[0]).rstrip() if fila[0] else "")
        if len(lineas) >= max_lines:
            lineas.append(
                f"\n[... salida truncada a {max_lines} líneas. "
                f"Pasá max_lines más alto para ver el resto. ...]"
            )
            break
 
    logger.info(
        "_read_spool_content: %s spool=%s num=%d — %d lineas",
        job_name, spooled_file_name, spooled_file_number, len(lineas),
    )
    return "\n".join(lineas)
    


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """Punto de entrada del servidor MCP."""
    mcp.run()


if __name__ == "__main__":
    main()

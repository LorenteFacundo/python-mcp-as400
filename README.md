# AS/400 MCP Server

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-purple.svg)](https://modelcontextprotocol.io/)

> Servidor MCP (Model Context Protocol) que conecta asistentes de IA con sistemas **AS/400 (IBM i)** de forma segura via ODBC.

Permite que un asistente de IA consulte bases de datos, lea codigo fuente, compile programas y analice spoolfiles en un IBM i — todo con controles de seguridad estrictos que impiden operaciones destructivas.

---

## Herramientas disponibles

| Herramienta | Descripcion | Ejemplo |
|---|---|---|
| `execute_sql` | Ejecuta consultas SQL de solo lectura | `SELECT * FROM QSYS2.SYSTABLES FETCH FIRST 5 ROWS ONLY` |
| `execute_cl_command` | Ejecuta comandos CL permitidos (whitelist) | `DSPFD FILE(MYLIB/MYFILE) OUTPUT(*OUTFILE) OUTFILE(QTEMP/FDA)` |
| `read_source_member` | Lee el codigo fuente de un miembro | `biblioteca='MYLIB', archivo_fuente='QRPGLESRC', miembro='MYPGM'` |
| `compile_program` | Compila un programa (RPG, CL, COBOL, C) | `library='MYLIB', member='MYPGM', command='CRTBNDRPG'` |
| `read_spoolfile` | Lee spoolfiles (resultados de compilacion) | Sin parametros lista los 10 mas recientes |

---

## Seguridad

Este servidor esta disenado para que la IA **ayude sin riesgo**. La filosofia es: analisis y compilacion si, destruccion nunca.

### SQL — Solo lectura

- Solo se permiten `SELECT`, `WITH` y `VALUES`
- Se bloquean `;` (statement stacking), `SELECT INTO`, `CALL`
- Se eliminan comentarios SQL antes de evaluar
- Si no se especifica limite, se agrega `FETCH FIRST 1000 ROWS ONLY` automaticamente

### Comandos CL — Whitelist estricta

Solo se permiten comandos especificos. Todo lo demas es rechazado.

**Permitidos:**

| Categoria | Comandos |
|---|---|
| Visualizacion | `DSP*` (DSPFD, DSPFFD, DSPPGMREF, DSPJOBLOG, DSPOBJD, DSPDBR, etc.) |
| Verificacion | `CHKOBJ` |
| Compilacion | `CRTBNDRPG`, `CRTRPGMOD`, `CRTBNDCL`, `CRTSQLRPGI`, `CRTPGM`, `CRTSRVPGM`, `CRTBNDCBL`, `CRTSQLCBLI`, `CRTCMOD`, `CRTBNDC` |
| Sesion | `ADDLIBLE`, `RMVLIBLE`, `CHGCURLIB` |
| Overrides | `OVRDBF`, `DLTOVR` |

**Bloqueados (ejemplos):**
`DLTOBJ`, `DLTF`, `DLTLIB`, `CLRPFM`, `CHGOBJ`, `GRTOBJAUT`, `RVKOBJAUT`, `CALL`, `SBMJOB`, `CPYF`, `MOVOBJ`, `RNMOBJ`, `RUNSQLSTM` y cualquier otro comando no listado arriba.

### Identificadores validados

Todos los nombres de biblioteca, archivo y miembro se validan contra el patron AS/400: 1-10 caracteres, alfanumerico + `#`, `@`, `$`. Esto previene inyeccion SQL en sentencias DDL.

### Errores sanitizados

Los mensajes de error nunca exponen credenciales, connection strings ni rutas internas del sistema.

---

## Prerequisitos

- **Python 3.10** o superior
- **IBM i Access ODBC Driver** instalado ([descargar de IBM](https://www.ibm.com/support/pages/ibm-i-access-client-solutions))
- Acceso a un sistema **IBM i (AS/400)** con usuario y contrasena

---

## Instalacion

### 1. Clonar el repositorio

```bash
git clone https://github.com/tu-usuario/as400-mcp-server.git
cd as400-mcp-server
```

### 2. Crear entorno virtual e instalar dependencias

```bash
python -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows

pip install -e .
```

### 3. Configurar credenciales

```bash
cp .env.example .env
# Editar .env con tus credenciales de IBM i
```

### 4. Probar que funciona

```bash
python server.py
```

El servidor arranca via stdio esperando mensajes MCP (JSON-RPC).

---

## Configuracion con Claude Desktop

Agrega lo siguiente a tu archivo de configuracion de Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "as400": {
      "command": "python",
      "args": ["C:/ruta/a/tu/as400-mcp-server/server.py"],
      "env": {
        "DB_HOST": "tu-host-ibmi.com",
        "DB_USER": "TU_USUARIO",
        "DB_PASS": "TU_PASSWORD"
      }
    }
  }
}
```

> **Nota:** Tambien podes definir las variables en `.env` en vez de en el JSON.

---

## Ejemplos de uso

### Consultar tablas del sistema

```
"Mostrame las primeras 10 tablas de la biblioteca MYLIB"
→ execute_sql("SELECT TABLE_NAME, TABLE_TYPE FROM QSYS2.SYSTABLES WHERE TABLE_SCHEMA = 'MYLIB' FETCH FIRST 10 ROWS ONLY")
```

### Ver estructura de un archivo

```
"Que campos tiene el archivo CUSTOMERS en MYLIB?"
→ execute_cl_command("DSPFFD FILE(MYLIB/CUSTOMERS) OUTPUT(*OUTFILE) OUTFILE(QTEMP/FLD)")
→ execute_sql("SELECT * FROM QTEMP.FLD")
```

### Leer codigo fuente

```
"Mostrame el codigo del programa CALCPRICE"
→ read_source_member(biblioteca='MYLIB', archivo_fuente='QRPGLESRC', miembro='CALCPRICE')
```

### Compilar y ver errores

```
"Compila el programa CALCPRICE y decime si hay errores"
→ compile_program(library='MYLIB', source_file='QRPGLESRC', member='CALCPRICE')
→ read_spoolfile()
```

---

## Estructura del proyecto

```
as400-mcp-server/
├── server.py           # Servidor MCP principal
├── .env.example        # Template de configuracion
├── .gitignore          # Archivos excluidos de git
├── pyproject.toml      # Metadata del proyecto Python
├── LICENSE             # Licencia MIT
└── README.md           # Esta documentacion
```

---

## Variables de entorno

| Variable | Descripcion | Requerida |
|---|---|---|
| `DB_HOST` | Hostname o IP del sistema IBM i | Si |
| `DB_USER` | Usuario de IBM i | Si |
| `DB_PASS` | Contrasena de IBM i | Si |

---

## Contribuir

Las contribuciones son bienvenidas. Si queres agregar un comando CL a la whitelist o una nueva herramienta:

1. Fork del repositorio
2. Crea una branch (`git checkout -b feature/mi-feature`)
3. Commit de tus cambios (`git commit -m 'Agregar mi feature'`)
4. Push a la branch (`git push origin feature/mi-feature`)
5. Abri un Pull Request

---

## Licencia

Este proyecto esta bajo la [Licencia MIT](LICENSE).

import shutil, tempfile, win32com.client

# Copie dans un temp pour éviter le conflit avec ADN en cours d'exécution
SDF_ORIG = r"C:\ADN_Evaluation\Synchro\SDLDEMO\ADN_DIAG.sdf"
_tmp = tempfile.mktemp(suffix=".sdf")
shutil.copy2(SDF_ORIG, _tmp)
SDF = _tmp

CONN_STR = f"Provider=Microsoft.SQLSERVER.CE.OLEDB.3.5;Data Source={SDF};"

conn = win32com.client.Dispatch("ADODB.Connection")
conn.Open(CONN_STR)

def columns(table: str) -> list[str]:
    rs = win32com.client.Dispatch("ADODB.Recordset")
    sql = "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='" + table + "' ORDER BY ORDINAL_POSITION"
    rs.Open(sql, conn)
    result = []
    while not rs.EOF:
        result.append(rs.Fields("COLUMN_NAME").Value)
        rs.MoveNext()
    rs.Close()
    return result

def query(sql: str) -> list[dict]:
    rs = win32com.client.Dispatch("ADODB.Recordset")
    rs.Open(sql, conn, 3, 1)   # adOpenStatic, adLockReadOnly
    rows = []
    while not rs.EOF:
        row = {rs.Fields(i).Name: rs.Fields(i).Value for i in range(rs.Fields.Count)}
        rows.append(row)
        rs.MoveNext()
    rs.Close()
    return rows

for t in ["Mission", "DPE", "DossierBien", "DPEParamGeneral"]:
    print(f"\n=== {t} ===")
    print(columns(t))

# Essai de lecture réelle
print("\n\n=== Lignes Mission ===")
try:
    rows = query("SELECT * FROM Mission")
    print(f"{len(rows)} lignes")
    if rows:
        print(rows[0])
except Exception as e:
    print("ERREUR:", e)

conn.Close()

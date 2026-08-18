Set-Location -LiteralPath $PSScriptRoot
$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) {
  py -3 -m pip install -r requirements.txt
  py -3 .\app.py
} else {
  python -m pip install -r requirements.txt
  python .\app.py
}

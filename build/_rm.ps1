$path='C:\Sythion\sythion\build\staging\python-downloads\python-3.11.9-embed-amd64.zip'
try { Remove-Item -LiteralPath $path -Force; Write-Host removed } catch { Write-Host ("err " + $_.Exception.Message) }

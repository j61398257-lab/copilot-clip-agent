# 在开发电脑上把已安装的 CodeGraph Windows 平台包打成 zip。输出放在 D 盘。
# zip 根目录直接是 node.exe 和 lib，解压到程序目录的 tools\codegraph 即可使用。
param([string]$Package = "")
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Work = "D:\copilot-clip-deps"
if (-not $Package) {
    $Prefix = (& npm prefix -g).Trim()
    $Package = Join-Path $Prefix "node_modules\@colbymchenry\codegraph\node_modules\@colbymchenry\codegraph-win32-x64"
}
foreach ($Need in @("node.exe", "lib\dist\bin\codegraph.js", "package.json")) {
    if (-not (Test-Path (Join-Path $Package $Need))) { throw "missing $Need in $Package" }
}
$Version = (Get-Content (Join-Path $Package "package.json") -Raw | ConvertFrom-Json).version
$Name = "codegraph-win32-x64-$Version.zip"
New-Item -ItemType Directory -Force -Path $Work | Out-Null
$Zip = Join-Path $Work $Name
if (Test-Path $Zip) { Remove-Item -Force $Zip }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($Package, $Zip)
$ZipHash = (Get-FileHash $Zip -Algorithm SHA256).Hash.ToLower()

$SumsPath = Join-Path $Repo "deps\SHA256SUMS"
$Keep = @()
if (Test-Path $SumsPath) {
    $Keep = @(Get-Content $SumsPath -Encoding UTF8 | Where-Object { $_ -and $_ -notmatch "codegraph-win32-x64-" })
}
$Sums = (($Keep + "$ZipHash  $Name") -join "`n") + "`n"
[System.IO.File]::WriteAllText($SumsPath, $Sums, [System.Text.UTF8Encoding]::new($false))
Write-Host "zip $Zip"
Write-Host "sha256 $ZipHash"

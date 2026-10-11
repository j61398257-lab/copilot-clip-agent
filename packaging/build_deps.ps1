# 在开发电脑上打包文档组件。下载放在 D 盘，不写 pip 缓存。
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Work = "D:\copilot-clip-deps"
$Wheels = Join-Path $Work "wheels"
$Stage = Join-Path $Work "stage"
if (Test-Path $Wheels) { Remove-Item -Recurse -Force $Wheels }
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force -Path $Wheels, $Stage | Out-Null

$Spec = "markitdown[docx,pdf,pptx,xlsx,xls,outlook]==0.1.7"
$Versions = @("310", "311", "312", "313", "314")
$Included = @()
foreach ($Version in $Versions) {
    $One = Join-Path $Work $Version
    if (Test-Path $One) { Remove-Item -Recurse -Force $One }
    New-Item -ItemType Directory -Force -Path $One | Out-Null
    Write-Host "download $Version"
    & python -m pip download --no-cache-dir --only-binary=:all: `
        -i "https://pypi.tuna.tsinghua.edu.cn/simple" --trusted-host "pypi.tuna.tsinghua.edu.cn" `
        --platform win_amd64 --python-version $Version --implementation cp `
        --abi "cp$Version" --abi none `
        --dest $One $Spec
    if ($LASTEXITCODE -ne 0) {
        Write-Host "skip $Version"
        continue
    }
    Copy-Item (Join-Path $One "*") $Wheels
    $Included += $Version
}
if ($Included.Count -eq 0) {
    throw "no wheels downloaded"
}

$Lines = @(Get-ChildItem $Wheels -File | Sort-Object Name | ForEach-Object {
    $Hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
    Copy-Item $_.FullName (Join-Path $Stage $_.Name)
    "$Hash  $($_.Name)"
})
$Manifest = ($Lines -join "`n") + "`n"
[System.IO.File]::WriteAllText((Join-Path $Stage "manifest.txt"), $Manifest, [System.Text.UTF8Encoding]::new($false))

$Zip = Join-Path $Work "docs-deps-win64.zip"
if (Test-Path $Zip) { Remove-Item -Force $Zip }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($Stage, $Zip)
$ZipHash = (Get-FileHash $Zip -Algorithm SHA256).Hash.ToLower()
$SumsDir = Join-Path $Repo "deps"
New-Item -ItemType Directory -Force -Path $SumsDir | Out-Null
$SumsPath = Join-Path $SumsDir "SHA256SUMS"
$Keep = @()
if (Test-Path $SumsPath) {
    $Keep = @(Get-Content $SumsPath -Encoding UTF8 | Where-Object { $_ -and $_ -notmatch "docs-deps-win64\.zip" -and $_ -notmatch "^# python" })
}
$Sums = ((@("$ZipHash  docs-deps-win64.zip", "# python $(($Included -join ','))") + $Keep) -join "`n") + "`n"
[System.IO.File]::WriteAllText($SumsPath, $Sums, [System.Text.UTF8Encoding]::new($false))
Write-Host "zip $Zip"
Write-Host "sha256 $ZipHash"
Write-Host "python $($Included -join ',')"

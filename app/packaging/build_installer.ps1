<#
.SYNOPSIS
Builds Refrain's installer (SPEC §13, §36 "10."): the app published with Native AOT for the installer
(-p:RefrainRelease=true: its data is %LOCALAPPDATA%\Refrain), the right-click handler DLL and the keyword's helper
beside it, the release gate, the wizard images, the Inno Setup installer and its checksum.

.DESCRIPTION
The release gate (AGENTS.md rule 9): a copy of the published folder outside the repository runs
`Refrain.exe --self-test` on an empty data folder of its own; a failure stops the build, and no installer is made.
By default the installer and SHA256SUMS.txt go to dist\v<version>\ (the local release), older dist\v* folders are
deleted and so is the intermediate build\; -Output puts them elsewhere (a check build) and deletes nothing in dist\.

Needs the .NET 10 SDK, Visual Studio's C++ build tools (Native AOT links with them; vswhere.exe is put on the PATH
here), and Inno Setup 6 (ISCC.exe in Program Files, or set ISCC to its path). Run from anywhere:
    powershell -ExecutionPolicy Bypass -File app\packaging\build_installer.ps1 [-Output <folder>] [-SelfTestOnly] [-KeepBuild]
#>
param(
    [string]$Output,
    [switch]$SelfTestOnly,
    [switch]$KeepBuild
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$app = Join-Path $root 'app'
$build = Join-Path $root 'build'
$publish = Join-Path $build 'Refrain'
$version = ([xml](Get-Content (Join-Path $app 'Directory.Build.props'))).Project.PropertyGroup[0].Version
$env:PATH = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer;$env:PATH"

function Publish([string]$project, [string]$to) {
    & dotnet publish (Join-Path $app $project) -c Release -p:RefrainRelease=true -p:DebugType=none -o $to -nologo -v q
    if ($LASTEXITCODE -ne 0) { throw "dotnet publish $project failed" }
}

# -- the published app, with the handler and the keyword's helper beside it --
if (Test-Path $build) { Remove-Item -Recurse -Force $build }
Publish 'Refrain\Refrain.csproj' $publish
Publish 'RefrainMenu\RefrainMenu.csproj' (Join-Path $build 'menu')
Publish 'RefrainHere\RefrainHere.csproj' (Join-Path $build 'here')
Copy-Item (Join-Path $build 'menu\RefrainMenu.dll') $publish
Copy-Item (Join-Path $build 'here\RefrainHere.exe') $publish
Get-ChildItem $publish -Recurse -Include '*.pdb' | Remove-Item -Force

# -- the release gate: a copy outside the repository, on its own empty data folder --
$gate = Join-Path ([IO.Path]::GetTempPath()) ("refrain-gate-" + [Guid]::NewGuid().ToString('N').Substring(0, 8))
try {
    Copy-Item -Recurse $publish (Join-Path $gate 'Refrain')
    $report = Join-Path $gate 'self-test.txt'
    $env:REFRAIN_DATA = Join-Path $gate 'data'
    $process = Start-Process (Join-Path $gate 'Refrain\Refrain.exe') -ArgumentList '--self-test', "`"$report`"" `
        -WorkingDirectory $gate -PassThru -Wait
    if (Test-Path $report) { Get-Content $report -Encoding UTF8 | Write-Host } else { Write-Host 'The self-test wrote no report.' }
    if ($process.ExitCode -ne 0) { throw "The release gate failed (exit code $($process.ExitCode)): no installer is built." }

    # The window too, which the self-test can't open: started off-screen (beyond every monitor, never the focus), it
    # must still run after 12 s with no error in its log. A published build once closed at start on a XAML resource.
    $env:REFRAIN_OFFSCREEN = '1'
    $window = Start-Process (Join-Path $gate 'Refrain\Refrain.exe') -WorkingDirectory $gate -PassThru
    Start-Sleep -Seconds 12
    $running = -not $window.HasExited
    if ($running) { Stop-Process -Id $window.Id -Force; $window.WaitForExit() }
    Remove-Item Env:\REFRAIN_OFFSCREEN
    $errors = Get-ChildItem (Join-Path $gate 'data\logs') -File -ErrorAction SilentlyContinue |
        ForEach-Object { Get-Content $_.FullName -Encoding UTF8 } | Where-Object { $_ -match ' ERROR ' }
    if (-not $running -or $errors) {
        $errors | Write-Host
        throw 'The release gate failed: the window did not start cleanly. No installer is built.'
    }
    Write-Host 'ok    the main window starts and runs (off-screen, 12 s, no error logged)'
    Remove-Item Env:\REFRAIN_DATA
}
finally {
    if (Test-Path $gate) { Remove-Item -Recurse -Force $gate }
}
if ($SelfTestOnly) {
    if (-not $KeepBuild) { Remove-Item -Recurse -Force $build }
    Write-Host 'The release gate passed (no installer built: -SelfTestOnly).'
    exit 0
}

# -- the wizard images, at every display scale: coral with the white sign, and the icon on white --
Add-Type -AssemblyName System.Drawing
$wizard = Join-Path $build 'wizard'
New-Item -ItemType Directory -Force $wizard | Out-Null
$coral = [Drawing.Color]::FromArgb(0xE8, 0x61, 0x3C)
function Draw-Sign([Drawing.Graphics]$g, [bool]$tile) {
    # The sign on its 64-unit grid (docs/identity/logo-concepts/b-repeat-sign.svg): the tile, two bars, two dots.
    $white = [Drawing.Brushes]::White
    if ($tile) {
        $path = New-Object Drawing.Drawing2D.GraphicsPath
        $r = 24  # the corner's diameter
        $path.AddArc(4, 4, $r, $r, 180, 90); $path.AddArc(60 - $r, 4, $r, $r, 270, 90)
        $path.AddArc(60 - $r, 60 - $r, $r, $r, 0, 90); $path.AddArc(4, 60 - $r, $r, $r, 90, 90); $path.CloseFigure()
        $g.FillPath((New-Object Drawing.SolidBrush $coral), $path)
    }
    $g.FillRectangle($white, 17, 15, 9, 34)
    $g.FillRectangle($white, 30, 15, 4.5, 34)
    $g.FillEllipse($white, 38.5, 21.5, 9, 9)
    $g.FillEllipse($white, 38.5, 33.5, 9, 9)
}
foreach ($size in @(@(164, 314), @(192, 386), @(246, 459), @(273, 556), @(328, 604), @(355, 700), @(410, 797))) {
    $w, $h = $size
    $image = New-Object Drawing.Bitmap $w, $h, ([Drawing.Imaging.PixelFormat]::Format24bppRgb)
    $g = [Drawing.Graphics]::FromImage($image)
    $g.SmoothingMode = 'AntiAlias'
    $g.Clear($coral)
    $side = $w * 0.9
    $g.TranslateTransform(($w - $side) / 2, $h * 0.38 - $side / 2)
    $g.ScaleTransform($side / 64, $side / 64)
    Draw-Sign $g $false
    $image.Save((Join-Path $wizard "large-${w}x$h.bmp"), [Drawing.Imaging.ImageFormat]::Bmp)
    $g.Dispose(); $image.Dispose()
}
foreach ($size in @(@(55, 58), @(64, 68), @(83, 80), @(92, 97), @(110, 106), @(119, 123), @(138, 140))) {
    $w, $h = $size
    $image = New-Object Drawing.Bitmap $w, $h, ([Drawing.Imaging.PixelFormat]::Format24bppRgb)
    $g = [Drawing.Graphics]::FromImage($image)
    $g.SmoothingMode = 'AntiAlias'
    $g.Clear([Drawing.Color]::White)
    $side = [Math]::Min($w, $h)
    $g.TranslateTransform(($w - $side) / 2, ($h - $side) / 2)
    $g.ScaleTransform($side / 64, $side / 64)
    Draw-Sign $g $true
    $image.Save((Join-Path $wizard "small-${w}x$h.bmp"), [Drawing.Imaging.ImageFormat]::Bmp)
    $g.Dispose(); $image.Dispose()
}

# -- the installer and its checksum --
$iscc = @($env:ISCC, "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
    Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) { throw 'Inno Setup 6 was not found. Install it (winget install JRSoftware.InnoSetup) or set ISCC.' }
$release = if ($Output) { $Output } else { Join-Path $root "dist\v$version" }
New-Item -ItemType Directory -Force $release | Out-Null
& $iscc /Q "/DAppVersion=$version" "/DSource=$publish" "/DOutputDir=$release" "/DWizard=$wizard" (Join-Path $PSScriptRoot 'installer.iss')
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed' }
$installer = Join-Path $release "Refrain-Setup-$version.exe"
$hash = (Get-FileHash $installer -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText((Join-Path $release 'SHA256SUMS.txt'), "$hash  Refrain-Setup-$version.exe`n")

# -- hygiene (rule 12): the local release keeps only the newest version; the intermediate build goes --
if (-not $Output) {
    Get-ChildItem (Join-Path $root 'dist') -Directory | Where-Object { $_.Name -like 'v*' -and $_.FullName -ne $release } |
        Remove-Item -Recurse -Force
}
if (-not $KeepBuild) { Remove-Item -Recurse -Force $build }
Write-Host ("Built $installer ({0:N1} MB) and SHA256SUMS.txt" -f ((Get-Item $installer).Length / 1MB))

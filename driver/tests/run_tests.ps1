# Builds and runs the user-mode tests for the driver's buffer logic.
$ErrorActionPreference = 'Stop'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw 'Visual Studio with the C++ tools was not found.' }

# Load the x64 compiler environment into this session. vcvars looks for vswhere on PATH.
$env:PATH = "$(Split-Path $vswhere);$env:PATH"
$vcvars = Join-Path $vs 'VC\Auxiliary\Build\vcvars64.bat'
foreach ($line in (cmd /c "call `"$vcvars`" >nul && set")) {
    if ($line -match '^([^=]+)=(.*)$') { Set-Item "env:$($Matches[1])" $Matches[2] }
}

$out = Join-Path $PSScriptRoot '..\build\tests'
New-Item -ItemType Directory -Force $out | Out-Null
$exe = Join-Path $out 'noisering_test.exe'
cl /nologo /W4 /WX /EHsc /std:c++17 "/Fo$out\" "/Fe$exe" (Join-Path $PSScriptRoot 'noisering_test.cpp')
if ($LASTEXITCODE) { throw 'Test build failed.' }
& $exe
exit $LASTEXITCODE
